import cv2
import torch
import numpy as np
from pyquaternion import Quaternion

import traceback
import time
from pathlib import Path
import os
from sys import platform
from sys import platform, exit

import BeamNGRL
from BeamNGRL.utils.visualisation import Vis
from beamngpy import BeamNGpy, Scenario, Vehicle
from beamngpy.sensors import Lidar, Camera, Electrics, Accelerometer, Timer, Damage
import threading

ROOT_PATH = Path(BeamNGRL.__file__).parent
DATA_PATH = ROOT_PATH.parent / ('BeamNGRL/data' if platform == "win32" else 'data')
BNG_HOME = os.environ.get('BNG_HOME')


def get_beamng_default(
        car_model='offroad',
        start_pos=None,
        start_quat=None,
        car_make='sunburst',
        beamng_path=BNG_HOME,
        map_config=None,
        path_to_maps=DATA_PATH.__str__(),
        remote=False,
        host_IP=None,
        camera_config=None,
        lidar_config=None,
        accel_config=None,
        vesc_config=None,
        burn_time=0.02,
        run_lockstep=False,
        traffic_config=None
):

    if(start_pos is None):
        print("please provide a start pos! I can not spawn a car in the ether!")
        exit()
    if(start_quat is None):
        print("please provide a start quat! I can not spawn a car's rotation in the ether!")
        exit()
    if(map_config is None):
        print("please provide a map_config! I can not spawn a car in the ether!")
        exit()
    map_rotate = False
    if "rotate" in map_config:
        map_rotate = map_config["rotate"]

    bng = beamng_interface(BeamNG_path=beamng_path, remote=remote, host_IP=host_IP, enable_traffic=traffic_config["enable"])
    bng.set_map_attributes(
        map_size=map_config["map_size"], resolution=map_config["map_res"], elevation_range=map_config["elevation_range"], path_to_maps=path_to_maps, rotate=map_rotate, map_name=map_config["map_name"]
    )
    bng.load_scenario(
        scenario_name=map_config["map_name"], car_make=car_make, car_model=car_model,
        start_pos=start_pos, start_rot=start_quat,
        camera_config=camera_config, lidar_config=lidar_config, accel_config=accel_config, vesc_config=vesc_config,
        traffic_config=traffic_config
    )
    bng.burn_time = burn_time
    bng.set_lockstep(run_lockstep)
    return bng

## this is the equivalent of None pizza with left beef joke. Yes I'd like one beamng simulator without the beamng simulator.
## https://en.wikipedia.org/wiki/None_pizza_with_left_beef
def get_beamng_nobeam(
        Dynamics,
        car_model='offroad',
        start_pos=None,
        start_quat=None,
        car_make='sunburst',
        beamng_path=BNG_HOME,
        map_config=None,
        path_to_maps=DATA_PATH.__str__(),
        remote=False, ## these options have no effect but are here for "compatibility"
        host_IP=None,
        camera_config=None,
        lidar_config=None,
        accel_config=None,
        vesc_config=None,
        burn_time=0.02,
        run_lockstep=False,
        traffic_config=None
):

    if(start_pos is None):
        print("please provide a start pos! I can not spawn a car in the ether!")
        exit()
    if(start_quat is None):
        print("please provide a start quat! I can not spawn a car's rotation in the ether!")
        exit()
    if(map_config is None):
        print("please provide a map_config! I can not spawn a car in the ether!")
        exit()
    map_rotate = False
    if "rotate" in map_config:
        map_rotate = map_config["rotate"]

    bng = beamng_interface(BeamNG_path=beamng_path, use_beamng=False, dyn=Dynamics, enable_traffic=traffic_config["enable"])
    bng.set_map_attributes(
        map_size=map_config["map_size"], resolution=map_config["map_res"], elevation_range=map_config["elevation_range"], path_to_maps=path_to_maps, rotate=map_rotate
    )
    bng.load_scenario(
        scenario_name=map_config["map_name"], car_make=car_make, car_model=car_model,
        start_pos=start_pos, start_rot=start_quat, traffic_config=traffic_config
    )
    bng.burn_time = burn_time
    bng.set_lockstep(run_lockstep)
    return bng


class beamng_interface():
    def __init__(self, BeamNG_path=BNG_HOME, host='localhost', port=64256, use_beamng=True, dyn=None, remote=False, host_IP=None, shell_mode=False, HITL_mode=False, async_mode=False, enable_traffic=False):
        self.lockstep   = False
        self.lidar_list = []
        self.lidar_fps = 10
        self.new_lidar = False
        self.last_lidar_time = 0
        self.camera_list = []
        self.camera_fps = 30
        self.new_cam = False
        self.last_cam_time = 0
        self.cam_segmt = False
        self.state_init = False
        self.A = np.array([0,0,9.81])
        self.last_A     = np.copy(self.A)
        self.vel_wf     = np.zeros(3)
        self.last_vel_wf= np.zeros(3)
        self.quat       = np.array([1,0,0,0])
        self.Tnb, self.Tbn = self.calc_Transform(self.quat)
        self.depth      = None
        self.pos        = None
        self.color      = None
        self.segmt      = None
        self.lidar_pts  = None
        self.Gravity    = np.array([0,0,9.81])
        self.state      = None
        self.BEV_center = np.zeros(3)
        self.avg_wheelspeed = 0
        self.dt = 0.02
        self.last_whspd_error = 0
        self.whspd_error_sigma = 0
        self.whspd_error_diff = 0
        self.elev_map_hgt = 2.0
        self.burn_time = 0.02
        self.use_vel_diff = True
        self.paused = False
        self.remote = remote
        self.lidar_config = None
        self.camera_config = None
        self.camera = False
        self.lidar = False
        self.use_sgmt = False
        self.steering_max = 260.0

        self.traffic_vehicles = {}
        self.traffic = enable_traffic

        self.use_beamng = use_beamng
        if self.use_beamng:
            if remote==True and host_IP is not None:
                self.bng = BeamNGpy(host_IP, 64256, remote=True)
                self.bng.open(launch=False, deploy=False)
            elif remote==True and host_IP is None:
                print("~Ara Ara! Trying to run BeamNG remotely without providing any host IP?")
                exit()
            else:
                self.bng = BeamNGpy(host, port, home=BeamNG_path, user=BeamNG_path + '/userfolder')
                self.bng.open()
        elif shell_mode:
            self.dyn = dyn
            self.state = torch.zeros(17, dtype=dyn.dtype, device=dyn.d)
            self.vis = Vis()

    def load_scenario(self, scenario_name='small_island', car_make='sunburst', car_model='offroad',
                      start_pos=np.array([-67, 336, 34.5]), start_rot=np.array([0, 0, 0.3826834, 0.9238795]),
                      camera_config=None, lidar_config=None, accel_config=None, vesc_config=None,
                      time_of_day=1200, hide_hud=False, traffic_config=None):
        self.start_pos = (start_pos[0], start_pos[1], self.get_height(start_pos))
        self.start_quat = start_rot
        if not self.use_beamng:
            self.state[:3] = torch.from_numpy(start_pos)
            self.state[3:6] = torch.from_numpy(self.rpy_from_quat(self.convert_beamng_to_REP103(start_rot)))
            return

        self.scenario = Scenario(scenario_name, name="test integration")

        self.vehicle = Vehicle('ego_vehicle', model=car_make, partConfig='vehicles/'+ car_make + '/' + car_model + '.pc')

        self.scenario.add_vehicle(self.vehicle, pos=(start_pos[0], start_pos[1], self.get_height(start_pos)),
                             rot_quat=(start_rot[0], start_rot[1], start_rot[2], start_rot[3]))
        
        # adds traffic vehicles to scenerio
        if self.traffic:
            num_traffic = len(traffic_config["start_poses"])
            if num_traffic != len(traffic_config["start_quats"]) or num_traffic != len(traffic_config["car_models"]) or num_traffic != len(traffic_config["car_makes"] or num_traffic != len(traffic_config["vids"])): 
                raise IndexError("The lists defining traffic cars (start_poses, start_quats, car_makes, car_models) in traffic_config don't have the same length. Make sure the lists have the same length and the coresponding indexes refer to the same traffic vehicle")
        
            for i in range(len(traffic_config["start_poses"])):
                vid = traffic_config["vids"][i]
                print(vid)
                traffic_start_pos = traffic_config["start_poses"][i]
                traffic_start_quat = traffic_config["start_quats"][i]
                self.traffic_vehicles[vid] = Vehicle(vid, model=traffic_config["car_makes"][i], partConfig='vehicles/'+ traffic_config["car_makes"][i] + '/' + traffic_config["car_models"][i] + '.pc')
                self.scenario.add_vehicle(self.traffic_vehicles[vid], pos=(traffic_start_pos[0], traffic_start_pos[1], self.get_height(traffic_start_pos)),
                    rot_quat=(traffic_start_quat[0], traffic_start_quat[1], traffic_start_quat[2], traffic_start_quat[3]))

        self.bng.set_tod(time_of_day/2400)

        self.bng.set_deterministic()

        self.scenario.make(self.bng)
        if(hide_hud):
            self.bng.hide_hud()
        # Create an Electrics sensor and attach it to the vehicle
        self.electrics = Electrics()
        self.timer = Timer()
        self.damage = Damage()
        self.vehicle.attach_sensor('electrics', self.electrics)
        self.vehicle.attach_sensor('timer', self.timer)
        self.vehicle.attach_sensor('damage', self.damage)
        self.bng.load_scenario(self.scenario)
        self.bng.start_scenario()

        # adds sensors to traffic cars
        if self.traffic:
            self.traffic_electrics = {}
            self.traffic_timer = {}
            self.traffic_damage = {}
            for vid, v in self.traffic_vehicles.items():
                self.traffic_electrics[vid] = Electrics()
                self.traffic_timer[vid] = Timer()
                self.traffic_damage[vid] = Damage()

                v.attach_sensor('electrics', self.traffic_electrics[vid])
                v.attach_sensor('timer', self.traffic_timer[vid])
                v.attach_sensor('damage', self.traffic_damage[vid])

        if accel_config == None:
            base_pos = (0,0,0.8)
        else:
            base_pos = self.ROS2BNG_bf_pos(accel_config["pos"],(0,0,0))

        self.camera_config = camera_config
        self.lidar_config = lidar_config
        self.vesc_config = vesc_config
        if self.vesc_config is not None:
            self.steering_max = self.vesc_config["steering_degrees"]
            
        if self.camera_config is not None and self.camera_config["enable"]:
            self.camera = True
            self.camera_fps = self.camera_config["fps"]
            cam_pos = self.ROS2BNG_bf_pos(self.camera_config["pos"], base_pos)
            self.use_sgmt = self.camera_config["annotation"]
            self.attach_camera(name='camera', pos=cam_pos, update_frequency=self.camera_fps, dir=self.camera_config["dir"], up=self.camera_config["up"], 
                               field_of_view_y=self.camera_config["fov"], resolution=(self.camera_config["width"],self.camera_config["height"]),
                               annotation=self.use_sgmt)

        if self.lidar_config is not None and self.lidar_config["enable"]:
            self.lidar = True
            self.lidar_fps = self.lidar_config["fps"]
            lidar_pos = self.ROS2BNG_bf_pos(self.lidar_config["pos"], base_pos)
            self.attach_lidar("lidar", pos=lidar_pos, dir=self.lidar_config["dir"], up=self.lidar_config["up"], vertical_resolution=self.lidar_config["channels"],
                             vertical_angle = self.lidar_config["vertical_angle"], rays_per_second_per_scan=self.lidar_config["rays_per_second_per_scan"],
                             update_frequency=self.lidar_fps, max_distance=self.lidar_config["max_distance"])

        self.state_poll()
        self.flipped_over = False

        # starts traffic and switches to driver vehicle
        if self.traffic:
            self.bng.start_traffic(list(self.traffic_vehicles.values()))
            self.bng.switch_vehicle(self.vehicle)

    def set_map_attributes(self, map_size = 16, resolution = 0.25, path_to_maps=DATA_PATH.__str__(), rotate=False, elevation_range=2.0, map_name="small_island"):
        self.elevation_map_full = np.load(path_to_maps + f'/map_data/{map_name}/elevation_map.npy', allow_pickle=True)
        self.color_map_full = cv2.imread(path_to_maps + f'/map_data/{map_name}/color_map.png')
        self.segmt_map_full = cv2.imread(path_to_maps + f'/map_data/{map_name}/segmt_map.png')
        self.path_map_full  = cv2.imread(path_to_maps + f'/map_data/{map_name}/paths.png')
        self.image_shape    = self.color_map_full.shape
        self.image_resolution = 0.1  # this is the original meters per pixel resolution of the image
        self.resolution     = resolution  # meters per pixel of the target map
        self.resolution_inv = 1/self.resolution  # pixels per meter
        self.map_size       = map_size/2  # 16 x 16 m grid around the car by default
        self.rotate = rotate
        self.elev_map_hgt = elevation_range

        if(self.image_resolution != self.resolution):
            scale_factor = self.image_resolution/self.resolution
            new_shape = np.array(np.array(self.image_shape) * scale_factor, dtype=np.int32)
            self.elevation_map_full = cv2.resize(self.elevation_map_full, (new_shape[0], new_shape[1]), cv2.INTER_AREA)
            self.color_map_full = cv2.resize(self.color_map_full, (new_shape[0], new_shape[1]), cv2.INTER_AREA)
            self.segmt_map_full = cv2.resize(self.segmt_map_full, (new_shape[0], new_shape[1]), cv2.INTER_AREA)
            self.path_map_full  = cv2.resize(self.path_map_full, (new_shape[0], new_shape[1]), cv2.INTER_AREA)
            self.image_shape    = (new_shape[0], new_shape[1])

        self.map_size_px = int(self.map_size*self.resolution_inv)
        self.map_size_px = (self.map_size_px, self.map_size_px)
        self.mask_size   = (2 * self.map_size_px[0], 2 * self.map_size_px[1])
        mask             = np.zeros(self.mask_size, np.uint8)
        self.mask        = cv2.circle(mask, self.map_size_px, self.map_size_px[0], 255, thickness=-1)
        self.mask_center = (self.map_size_px[0], self.map_size_px[1])

        self.inpaint_mask = np.zeros_like(self.elevation_map_full, dtype=np.uint8)
        index = np.where(self.elevation_map_full == 0)
        self.inpaint_mask[index] = 255

        # creates marker image
        self.marker_width = int(self.map_size*self.resolution_inv/8)
        self.overlay_image = np.zeros([self.marker_width, self.marker_width, 3])
        cv2.rectangle(self.overlay_image, (int(self.marker_width / 3), 0), (int(self.marker_width * 2 / 3), self.marker_width), (255, 255, 255), -1) 
        cv2.circle(self.overlay_image, (int(self.marker_width / 2), int(self.marker_width / 4)), int(self.marker_width / 4), (255, 255, 255), -1)

    def get_map_bf_no_rp(self, map_img, gen_mask=False, inpaint_mask = None):
        ch = len(map_img.shape)
        if(ch==3):
            BEV = map_img[self.Y_min:self.Y_max, self.X_min:self.X_max, :]
        else:
            BEV = map_img[self.Y_min:self.Y_max, self.X_min:self.X_max]

        if inpaint_mask is not None:
            BEV = cv2.inpaint(BEV, inpaint_mask, ch, cv2.INPAINT_TELEA)
            
        if(self.rotate):
            # get rotation matrix using yaw:
            rotate_matrix = cv2.getRotationMatrix2D(center=self.mask_center, angle= self.rpy[2]*57.3, scale=1)
            # rotate the image using cv2.warpAffine
            BEV = cv2.warpAffine(src=BEV, M=rotate_matrix, dsize=self.mask_size)
            # mask:
            if not gen_mask:
                BEV = cv2.bitwise_and(BEV, BEV, mask=self.mask)

        return BEV

    def transform_world_to_bodyframe(x, y, xw, yw, th):
        x -= xw
        y -= yw
        R = np.zeros((2,2))
        ct, st = np.cos(-th), np.sin(-th)
        R[0,0], R[0,1], R[1,0], R[1,1] = ct, -st, st, ct
        X = np.array(x)
        Y = np.array(y)
        V = np.array([X,Y])
        O = np.matmul(R, V)
        x, y = O[0,:], O[1,:]
        return x, y

    def ROS2BNG_bf_pos(self, pos, base_pos):
        return  (pos[1] + base_pos[1], -pos[0] + base_pos[1], pos[2] + base_pos[2])
 
    def get_height(self, pos):
        elevation_img_X = np.clip(int( pos[0]*self.resolution_inv + self.image_shape[0]//2), self.map_size*self.resolution_inv, self.image_shape[0] - 1 - self.map_size*self.resolution_inv)
        elevation_img_Y = np.clip(int( pos[1]*self.resolution_inv + self.image_shape[1]//2), self.map_size*self.resolution_inv, self.image_shape[0] - 1 - self.map_size*self.resolution_inv)

        return self.elevation_map_full[int( np.round(elevation_img_Y) ), int( np.round(elevation_img_X) )]

    def gen_BEVmap(self):

        self.img_X = np.clip(int( self.pos[0]*self.resolution_inv + self.image_shape[0]//2), self.map_size*self.resolution_inv, self.image_shape[0] - 1 - self.map_size*self.resolution_inv)
        self.img_Y = np.clip(int( self.pos[1]*self.resolution_inv + self.image_shape[1]//2), self.map_size*self.resolution_inv, self.image_shape[0] - 1 - self.map_size*self.resolution_inv)

        self.Y_min = int(self.img_Y - self.map_size*self.resolution_inv)
        self.Y_max = int(self.img_Y + self.map_size*self.resolution_inv)

        self.X_min = int(self.img_X - self.map_size*self.resolution_inv)
        self.X_max = int(self.img_X + self.map_size*self.resolution_inv)

        ## inputs:
        local_inpaint = self.get_map_bf_no_rp(self.inpaint_mask, gen_mask=True)
        self.BEV_color = self.get_map_bf_no_rp(self.color_map_full, inpaint_mask=local_inpaint)  # crops circle, rotates into body frame
        self.BEV_heght = self.get_map_bf_no_rp(self.elevation_map_full, inpaint_mask=local_inpaint)
        self.BEV_segmt = self.get_map_bf_no_rp(self.segmt_map_full, inpaint_mask=local_inpaint)
        self.BEV_path  = self.get_map_bf_no_rp(self.path_map_full)

        # car overlay on map
        marker_size = int(self.map_size*self.resolution_inv/16)
        car_shapes = np.zeros_like(self.BEV_color, np.uint8)
        car_shapes = np.pad(car_shapes, ((marker_size, marker_size), (marker_size, marker_size), (0, 0)), 'edge')

        if self.traffic:
            self.img_X_traffic = {} 
            self.img_Y_traffic = {}

            self.Y_min_traffic = {}
            self.Y_max_traffic = {}

            self.X_min_traffic = {}
            self.X_max_traffic = {}
            for vid, v in self.traffic_vehicles.items():
                if (vid in self.traffic_pos ):
                    self.img_X_traffic[vid] = np.clip(int( self.traffic_pos[vid][0]*self.resolution_inv + self.image_shape[0]//2), self.map_size*self.resolution_inv, self.image_shape[0] - 1 - self.map_size*self.resolution_inv)
                    self.img_Y_traffic[vid] = np.clip(int( self.traffic_pos[vid][1]*self.resolution_inv + self.image_shape[1]//2), self.map_size*self.resolution_inv, self.image_shape[0] - 1 - self.map_size*self.resolution_inv)

                    self.Y_min_traffic[vid] = int(self.img_Y_traffic[vid] - self.Y_min - marker_size) + marker_size
                    self.Y_max_traffic[vid] = int(self.img_Y_traffic[vid] - self.Y_min + marker_size) + marker_size

                    self.X_min_traffic[vid] = int(self.img_X_traffic[vid] - self.X_min - marker_size) + marker_size
                    self.X_max_traffic[vid] = int(self.img_X_traffic[vid] - self.X_min + marker_size) + marker_size

                    self.traffic_marker_rotation = - self.traffic_rpy[vid][2] * 180 / np.pi + 90

                    image_center = tuple(np.array(self.overlay_image.shape[1::-1]) / 2)
                    rot_mat = cv2.getRotationMatrix2D(image_center, int(self.traffic_marker_rotation), 1.0)
                    result = cv2.warpAffine(self.overlay_image, rot_mat, self.overlay_image.shape[1::-1], flags=cv2.INTER_LINEAR)

                    car_shapes[self.Y_min_traffic[vid]:self.Y_max_traffic[vid], self.X_min_traffic[vid]:self.X_max_traffic[vid]] = result

        # adds marker for controlled car
        self.Y_min_marker = int(self.img_Y - self.Y_min - self.map_size*self.resolution_inv/16) + marker_size
        self.Y_max_marker = int(self.img_Y - self.Y_min + self.map_size*self.resolution_inv/16) + marker_size

        self.X_min_marker = int(self.img_X - self.X_min - self.map_size*self.resolution_inv/16) + marker_size
        self.X_max_marker = int(self.img_X - self.X_min + self.map_size*self.resolution_inv/16) + marker_size

        self.marker_rotation = - self.rpy[2] * 180 / np.pi + 90

        image_center = tuple(np.array(self.overlay_image.shape[1::-1]) / 2)
        rot_mat = cv2.getRotationMatrix2D(image_center, int(self.marker_rotation), 1.0)
        result = cv2.warpAffine(self.overlay_image, rot_mat, self.overlay_image.shape[1::-1], flags=cv2.INTER_LINEAR)

        car_shapes[self.Y_min_marker:self.Y_max_marker, self.X_min_marker:self.X_max_marker] = result

        # applies mask
        car_shapes = car_shapes[marker_size:-marker_size, marker_size:-marker_size]
        mask = car_shapes.astype(bool)

        self.BEV_color[mask] = cv2.addWeighted(car_shapes, 0.7, self.BEV_color, 0.3, 0)[mask]

        self.BEV_center[:2] = self.pos[:2]
        self.BEV_center[2] = self.BEV_heght[self.map_size_px[0], self.map_size_px[1]]
        self.BEV_heght -= self.BEV_center[2]
        self.BEV_heght = np.clip(self.BEV_heght, -self.elev_map_hgt, self.elev_map_hgt)
        self.BEV_heght = np.nan_to_num(self.BEV_heght, copy=False, nan=0.0, posinf=self.elev_map_hgt, neginf=-self.elev_map_hgt)
        self.BEV_normal = self.compute_surface_normals()


    def compute_surface_normals(self):
        # Compute the gradient of the elevation map using the Sobel operator
        BEV_normal = np.copy(self.BEV_heght)
        BEV_normal = cv2.resize(BEV_normal, (int(self.map_size_px[0]*4), int(self.map_size_px[0]*4)), cv2.INTER_AREA)
        BEV_normal = cv2.GaussianBlur(BEV_normal, (3,3), 0)
        normal_x = -cv2.Sobel(BEV_normal, cv2.CV_64F, 1, 0, ksize=3)
        normal_y = -cv2.Sobel(BEV_normal, cv2.CV_64F, 0, 1, ksize=3)
        # Compute the normal vector as the cross product of the x and y gradients
        normal_z = np.ones_like(BEV_normal)
        normals = np.stack([normal_x, normal_y, normal_z], axis=-1)
        # Normalize the normal vectors
        norms = np.linalg.norm(normals, axis=-1, keepdims=True)
        normals = normals / norms
        normals = cv2.resize(normals, (int(self.map_size_px[0]*2), int(self.map_size_px[0]*2)), cv2.INTER_AREA)
        return normals

    def rpy_from_quat(self, quat):
        y = np.zeros(3)
        y[0] = np.arctan2((2.0*(quat[2]*quat[3]+quat[0]*quat[1])) , (quat[0]**2 - quat[1]**2 - quat[2]**2 + quat[3]**2))
        y[1] = -np.arcsin(2.0*(quat[1]*quat[3]-quat[0]*quat[2]));
        y[2] = np.arctan2((2.0*(quat[1]*quat[2]+quat[0]*quat[3])) , (quat[0]**2 + quat[1]**2 - quat[2]**2 - quat[3]**2))
        return y

    def quat_from_rpy(self, rpy):
        u1 = np.cos(0.5*rpy[0]);
        u2 = np.cos(0.5*rpy[1]);
        u3 = np.cos(0.5*rpy[2]);
        u4 = np.sin(0.5*rpy[0]);
        u5 = np.sin(0.5*rpy[1]);
        u6 = np.sin(0.5*rpy[2]);
        quat = np.zeros(4)
        quat[0] = u1*u2*u3+u4*u5*u6;
        quat[1] = u4*u2*u3-u1*u5*u6;
        quat[2] = u1*u5*u3+u4*u2*u6;
        quat[3] = u1*u2*u6-u4*u5*u3;
        return quat

    def convert_beamng_to_REP103(self, rot):
        rot = Quaternion(rot[2], -rot[0], -rot[1], -rot[3])
        new = Quaternion([0,np.sqrt(2)/2,np.sqrt(2)/2,0])*rot
        rot = Quaternion(-new[1], -new[3], -new[0], -new[2])
        return rot

    def calc_Transform(self, quat):
        q00 = quat[0]**2;
        q11 = quat[1]**2;
        q22 = quat[2]**2;
        q33 = quat[3]**2;
        q01 =  quat[0]*quat[1];
        q02 =  quat[0]*quat[2];
        q03 =  quat[0]*quat[3];
        q12 =  quat[1]*quat[2];
        q13 =  quat[1]*quat[3];
        q23 =  quat[2]*quat[3];

        Tbn = np.zeros((3,3)) # transform body->ned
        Tbn[0][0] = q00 + q11 - q22 - q33;
        Tbn[1][1] = q00 - q11 + q22 - q33;
        Tbn[2][2] = q00 - q11 - q22 + q33;
        Tbn[0][1] = 2*(q12 - q03);
        Tbn[0][2] = 2*(q13 + q02);
        Tbn[1][0] = 2*(q12 + q03);
        Tbn[1][2] = 2*(q23 - q01);
        Tbn[2][0] = 2*(q13 - q02);
        Tbn[2][1] = 2*(q23 + q01);

        Tnb = Tbn.transpose(); # transform ned->body
        return Tnb, Tbn

    def increase_brightness(self, img, value=30):
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        h, s, v = cv2.split(hsv)

        lim = 255 - value
        v[v > lim] = 255
        v[v <= lim] += value

        final_hsv = cv2.merge((h, s, v))
        img = cv2.cvtColor(final_hsv, cv2.COLOR_HSV2BGR)
        return img

    def attach_lidar(self, name, pos=(0,0,1.5), dir=(0,-1,0), up=(0,0,1), vertical_resolution=3, vertical_angle=26.9,
                     rays_per_second_per_scan=5000, update_frequency=10, max_distance=10.0):
        lidar = Lidar(name, self.bng, self.vehicle, pos = pos, dir=dir, up=up,requested_update_time=0.001, is_visualised=False,
                        vertical_resolution=3, vertical_angle=5, rays_per_second=vertical_resolution*rays_per_second_per_scan, max_distance=max_distance,
                        frequency=update_frequency, update_priority = 0,is_using_shared_memory=(not self.remote))
        self.lidar_list.append(lidar)
        print("lidar attached")

    def attach_camera(self, name, pos=(0,-2,1.4), dir=(0,-1,0), up=(0,0,1), field_of_view_y=87, resolution=(640,480),
                      depth=True, color=True, annotation=False, instance=False, near_far_planes=(0.15,60.0), update_frequency = 30, static=False):
        camera = Camera(name, self.bng, self.vehicle, pos=pos, dir=dir, up=up, field_of_view_y=field_of_view_y, resolution=resolution, update_priority=0,
                         is_render_colours=color, is_render_depth=depth, is_render_annotations=annotation,is_visualised=True,
                         requested_update_time=0.01, near_far_planes=near_far_planes, is_using_shared_memory=(not self.remote),
                         is_render_instance=instance,  is_static=static)
        self.camera_list.append(camera)
        print("camera attached")

    def attach_accelerometer(self, pos=(0, 0.0,0.8)):
        self.accel = Accelerometer('accel', self.bng, self.vehicle, pos =pos, requested_update_time=0.1, is_using_gravity=False)
        print("accel attached")

    def camera_poll(self, index):
        ## TODO: this function should "return" the images corresponding to that sensor, not just store them in "self.color/depth"
        try:
            camera_readings = self.camera_list[index].poll()
            color = camera_readings['colour']
            self.color = cv2.cvtColor(color, cv2.COLOR_BGR2RGB)
            self.depth = camera_readings['depth']
            if self.use_sgmt:
                self.segmt = camera_readings['annotation']
        except Exception as e:
            print(traceback.format_exc())

    def lidar_poll(self, index):
        ## TODO: this function should "return" the images corresponding to that sensor
        try:
            points = self.lidar_list[index].poll()
            self.lidar_pts = np.copy(points['pointCloud'])
        except Exception as e:
            print(traceback.format_exc())

    def Accelerometer_poll(self):
        ## TODO: this function should return the readings, not store them in a class variable to accomodate multi-agent simulation in the future.
        if not self.use_vel_diff:
            try:
                acc = self.accel.poll()
                if 'axis1' in acc: ## simulator tends to provide empty message on failure
                    temp_acc = np.array([acc['axis1'], acc['axis3'], -acc['axis2']])
                    if np.all(temp_acc) != 0: ## in case you're using my modified version of the simulator which sends all 0s on fault.
                        g_bf = np.matmul(self.Tnb, self.Gravity)
                        self.last_A = self.A
                        self.A = temp_acc + g_bf
                ## only update acceleration if we have a valid new reading.
                ## the accelerometer in bng 0.26 is unreliable, so I recommend using vel_diff method
            except Exception:
                print(traceback.format_exc())
        else:
            acc = (self.vel_wf - self.last_vel_wf)/self.dt
            self.last_vel_wf = np.copy(self.vel_wf)
            self.A = 0.2*np.matmul(self.Tnb, acc + self.Gravity) + 0.8*self.last_A
            self.last_A = np.copy(self.A)

    def set_lockstep(self, lockstep):
        self.lockstep = lockstep
        if self.lockstep:
            self.paused = False ## assume initially not paused
        else:
            self.paused = True ## assume initially paused

    def handle_timing(self):
        if(self.lockstep):
            if not self.paused:
                self.bng.pause()
                self.paused = True
            self.bng.step(1)
        else:
            if self.paused:
                self.bng.resume()
                self.paused = False

    def state_poll(self):
        try:
            if(self.state_init == False):
                assert self.burn_time != 0, "step time can't be 0"
                self.bng.set_steps_per_second(int(1/self.burn_time)) ## maximum steps per second; we can only guarantee this if running on a high perf. system.
                self.handle_timing()
                self.Accelerometer_poll()
                self.vehicle.poll_sensors() # Polls the data of all sensors attached to the vehicle
                self.state_init = True
                self.last_quat = self.convert_beamng_to_REP103(self.vehicle.state['rotation'])
                self.timestamp = self.vehicle.sensors['timer']['time']
                print("beautiful day, __init__?") ## being cheeky are we?

                # polls traffic vehicles sensors
                if self.traffic:
                    for vid, v in self.traffic_vehicles.items():
                        v.poll_sensors()
            else:
                self.handle_timing()
                self.Accelerometer_poll()
                self.vehicle.poll_sensors() # Polls the data of all sensors attached to the vehicle
                self.timestamp = self.vehicle.sensors['timer']['time'] ## time in seconds since the start of the simulation -- does not care about resets
                self.dt = max(self.vehicle.sensors['timer']['time'] - self.timestamp, 0.02)
                self.timestamp = self.vehicle.sensors['timer']['time'] ## time in seconds since the start of the simulation -- does not care about resets
                self.broken = self.vehicle.sensors['damage']['part_damage'] ## this is useful for reward functions
                self.pos = np.copy(self.vehicle.state['pos'])
                self.vel = np.copy(self.vehicle.state['vel'])
                self.quat = self.convert_beamng_to_REP103(np.copy(self.vehicle.state['rotation']))
                self.rpy = self.rpy_from_quat(self.quat)
                self.Tnb, self.Tbn = self.calc_Transform(self.quat)
                self.vel_wf = np.copy(self.vel)
                self.vel = np.matmul(self.Tnb, self.vel)
                diff = self.quat/self.last_quat
                self.last_quat = self.quat
                self.G = np.array([diff[1]*2/self.dt, diff[2]*2/self.dt, diff[3]*2/self.dt])  # gx gy gz
                self.G = np.matmul(self.Tnb, self.G)
                sign = np.sign(self.vehicle.sensors['electrics']['gear_index'])
                if sign == 0:
                    sign = 1 ## special case just to make sure we don't consider 0 speed in neutral gear
                self.avg_wheelspeed = self.vehicle.sensors['electrics']['wheelspeed'] * sign
                self.steering = float(self.vehicle.sensors['electrics']['steering']) / self.steering_max
                throttle = float(self.vehicle.sensors['electrics']['throttle'])
                brake = float(self.vehicle.sensors['electrics']['brake'])
                self.thbr = throttle - brake
                self.state = np.hstack((self.pos, self.rpy, self.vel, self.A, self.G, self.steering, self.thbr))
                
                # gets values for traffic sensors
                if self.traffic:
                    self.traffic_timestamp = {}
                    self.traffic_dt = {}
                    self.traffic_timestamp = {}
                    self.traffic_broken = {}
                    self.traffic_pos = {}
                    self.traffic_vel = {}
                    self.traffic_quat = {}
                    self.traffic_rpy = {}
                    self.traffic_Tnb = {}
                    self.traffic_Tbn = {}
                    self.traffic_vel_wf = {}
                    self.traffic_vel = {}
                    for vid, v in self.traffic_vehicles.items():
                        v.poll_sensors() # Polls the data of all sensors attached to the vehicle
                        if ((v.state["pos"][0] - self.pos[0]) ** 2 < self.map_size ** 2 and (v.state["pos"][1] - self.pos[1]) ** 2 < self.map_size ** 2):
                            self.traffic_timestamp[vid] = v.sensors['timer']['time'] ## time in seconds since the start of the simulation -- does not care about resets
                            self.traffic_dt[vid] = max(v.sensors['timer']['time'] - self.traffic_timestamp[vid], 0.02)
                            self.traffic_timestamp[vid] = v.sensors['timer']['time'] ## time in seconds since the start of the simulation -- does not care about resets
                            self.traffic_broken[vid] = v.sensors['damage']['part_damage'] ## this is useful for reward functions
                            self.traffic_pos[vid] = np.copy(v.state['pos'])
                            self.traffic_vel[vid] = np.copy(v.state['vel'])
                            self.traffic_quat[vid] = self.convert_beamng_to_REP103(np.copy(v.state['rotation']))
                            self.traffic_rpy[vid] = self.rpy_from_quat(self.traffic_quat[vid])
                            self.traffic_Tnb[vid], self.traffic_Tbn[vid] = self.calc_Transform(self.traffic_quat[vid])
                            self.traffic_vel_wf[vid] = np.copy(self.traffic_vel[vid])
                            self.traffic_vel[vid] = np.matmul(self.traffic_Tnb[vid], self.traffic_vel[vid])

                self.gen_BEVmap()
                if(abs(self.rpy[0]) > np.pi/2 or abs(self.rpy[1]) > np.pi/2):
                    self.flipped_over = True

                if self.camera:
                    if self.timestamp - self.last_cam_time > 1/self.camera_fps:
                        self.camera_poll(0)
                        self.last_cam_time = self.timestamp
                if self.lidar:
                    if self.timestamp - self.last_lidar_time > 1/self.lidar_fps:
                        self.lidar_poll(0)
                        self.last_lidar_time = self.timestamp
                        self.lidar_pts -= self.pos
                        self.lidar_pts = np.matmul(self.lidar_pts, self.Tnb.T)
        except Exception:
            print(traceback.format_exc())


    def scaled_PID_FF(self, Kp, Ki, Kd, FF_gain, FF, error, error_sigma, error_diff, last_error):
        error_sigma += error * self.dt
        error_sigma = np.clip(error_sigma, -1, 1) ## clip error_sigma to 10%
        ## innovation in error_derivative:
        diff_innov = np.clip((error - last_error)/self.dt, -1, 1) - error_diff
        ## smoothing error derivative:
        error_diff += diff_innov * 0.5
        PI = Kp * error + Ki * error_sigma + Kd * error_diff + FF * FF_gain
        return PI, error_sigma, error_diff

    def send_ctrl(self, action, speed_ctrl=False, speed_max = 1, Kp = 1, Ki =  1, Kd = 0, FF_gain = 1):
        st, th = -action[0], action[1]
        if(speed_ctrl):
            speed_err = th - (self.avg_wheelspeed/speed_max)
            th, self.whspd_error_sigma, self.whspd_error_diff = self.scaled_PID_FF(Kp, Ki, Kd, FF_gain, th, speed_err, self.whspd_error_sigma, self.whspd_error_diff, self.last_whspd_error)
            self.last_whspd_error = speed_err
            th = np.clip(th, -1,1)
        br = 0
        th_out = th
        if(th < 0):
            br = -th
            th_out = 0

        self.vehicle.control(throttle = th_out, brake = br, steering = st)

    def reset(self, start_pos = None, start_quat=None):
        if(start_pos is None):
            start_pos = np.copy(self.start_pos)
        if(start_quat is None):
            start_quat = np.copy(self.start_quat)
            self.vehicle.teleport(pos=(start_pos[0], start_pos[1], start_pos[2]) )
        else:
            self.vehicle.teleport(pos=(start_pos[0], start_pos[1], start_pos[2]), rot_quat= (start_quat[0], start_quat[1], start_quat[2], start_quat[3]) )
        self.vehicle.control(throttle = 0, brake = 0, steering = 0)
        self.flipped_over = False

        self.avg_wheelspeed = 0
        self.last_whspd_error = 0
        self.whspd_error_sigma = 0
        self.whspd_error_diff = 0


    def step(self, action):
        self.pos = self.state[:3].cpu().numpy()
        self.gen_BEVmap()

        BEV_heght = torch.from_numpy(self.BEV_heght).to(device=self.dyn.d, dtype=self.dyn.dtype)
        BEV_normal = torch.from_numpy(self.BEV_normal).to(device=self.dyn.d, dtype=self.dyn.dtype)
        self.dyn.set_BEV(BEV_heght, BEV_normal)

        offset = torch.clone(self.state[:3])
        self.state[:3] = 0
        padded_state = self.state[None, None, None, :]
        padded_action = action[None, None, None, :]

        self.state = self.dyn.forward(padded_state, padded_action)
        self.state = self.state.squeeze()
        self.state[:3] += offset

    def render(self, goal):
        vis_state = self.state.cpu().numpy()
        self.vis.setcar(pos=np.zeros(3), rpy=vis_state[3:6])
        self.vis.setgoal(goal - vis_state[:2])
        self.vis.set_terrain(self.BEV_heght, self.resolution, self.map_size)
