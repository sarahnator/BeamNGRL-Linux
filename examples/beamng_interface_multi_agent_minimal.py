import numpy as np
import cv2
from BeamNGRL.BeamNG.beamng_interface_multi_agent import *
import traceback
import argparse


def main(args):
    ## given x,y location, get the correct z location so users don't have to worry too much about getting exact coordinates
    ## make sure all the lists (vids, car_models...) are in the same order with indexes coresponding to the different agents
    agents_config = dict()
    agents_config = {
        "vids": ["ego", "auto", "traffic"],
        "ego_vid": "ego", ## vid of agent that will be controlled by player of the game/game display will focus on
        "traffic_vids": ["traffic"],
        "car_models": ["offroad", "offroad", "offroad"], 
        "car_makes": ["sunburst", "sunburst", "sunburst"],  ## car make (company/manufacturer)
        "start_poses": [np.array([-86.5, 323.26, 40.5]), np.array([-92.5, 303.26, 40.5]), np.array([-97.5, 303.26, 40.5])],   ## start position in ENU (east north up). Center of the map is usually 0,0, height is terrain dependent. TODO: use the map model to estimate terrain height.
        "start_quats": [np.array([0, 0, 0, 1]), np.array([0, 0, 0, 1]), np.array([0, 0, 0, 1])]   ## start quaternion -- TODO: there should be a ROS to BeamNG to ROS conversion system for reference frames.
    }

    Map_config = dict()
    Map_config = {
        "map_name": args.map_name,
        "map_size": 64,
        "map_res": 0.25,
        "map_res_hitl": 0.25,
        "elevation_range": 4.0,
        "layers": {"color": 3, "elevation": 1, "semantics": 3, "costmap": 1},
        "topic_name": "/grid_map_occlusion_inpainting/all_grid_map",
        "rotate": False,
    }

    camera_config = dict()
    lidar_config = dict()
    IMU_config = dict()
    camera_config = {
        "enable": False,
        "width": 640,
        "height": 480,
        "fps": 30,
        "fov": 87.0,
        "pos": [0.15, 0.047, 0.02],
        "dir": [0, -1, 0],
        "up": [0, 0, 1],
        "rot": [0, 0, 0, 1],
        "color_optical_frame": "camera_color_optical_frame",
        "depth_optical_frame": "camera_depth_optical_frame",
        "depth_frame": "camera_depth_frame",
        "camera_color_topic": "/camera/color/image_raw",
        "camera_depth_topic": "/camera/depth/image_rect_raw",
        "camera_color_info_topic": "/camera/color/camera_info",
        "camera_depth_info_topic": "/camera/depth/camera_info",
        "monitor_topic": "/camera/depth/image_rect_raw",
        "annotation": False,
    }

    lidar_config = {
        "enable": False,
        "rays_per_second_per_scan": 5000,
        "channels": 3,
        "fps": 10,
        "vertical_angle": 26.9,
        "pos": [0.04, 0, 0.07],
        "rot": [0, 0, 0, 1],
        "dir": [0, -1, 0],
        "up": [0, 0, 1],
        "frame": "laser_frame",
        "max_distance": 10.0,
        "scan_topic": "/scan",
        "monitor_topic": "/scan",
        "pc_topic": "converted_pc",
    }

    IMU_config = {
        "pos": [0, 0, 0.1],
        "fps": 50,
        "monitor_topic": "/mavros/imu/data_raw",
        "pose_topic": "/mavros/local_position/pose",
        "odom_topic": "/mavros/local_position/odom",
        "state_topic": "/mavros/state",
        "gps_topic": "/mavros/gpsstatus/gps1/raw",
        "notification_topic": "/mavros/play_tune",
        "channel_topic": "mavros/rc/in",
        "raw_input_topic": "/mavros/manual_control/send",
        "frame": "base_link",
        "failure_action": "rosrun mavros mavsys rate --all 50",
    }

    beamng_interface_multi_agent = get_beamng_default(
        agents_config=agents_config,
        map_config=Map_config,  ## Map config; this is "necessary"
        remote= args.remote,  ## are you running the simulator remotely (on a separate computer or on the same computer but outside the docker)?
        host_IP=args.host_IP,  ## if using a remote connection (usually the case when running sim on a separate computer)
        camera_config=camera_config,  ## currently, camera only works on windows, so you can only use this if you have the sim running remotely or you're using windows as the host
        lidar_config=lidar_config,  ## currently, lidar only works on windows, so you can only use this if the sim is running remotely or you're using a windows host
        accel_config=IMU_config,  ## IMU config. if left blank, a default config is used.
        burn_time=0.02,  ## step or dt time
        run_lockstep=False,  ## whether the simulator waits for control input to move forward in time. Set to true to have a gym "step" like functionality
    )

    # beamng_interface_multi_agent.set_lockstep(True) ## this is how you can change lockstepping modes during execution
    ## WARNING: when using lockstep = true, you will NOT be able to control the vehicle using the arrow keys (effectively, the simulator will only execute the commands being sent to it)
    while True:
        try:
            beamng_interface_multi_agent.handle_timing()

            # state is np.hstack((pos, rpy, vel, A, G, st, th/br)) ## note that velocity is in the body-frame
            # state information follows ROS REP103 standards (so basically ROS standards): world refernce frame for (x,y,z) is east-north-up(ENU). Body frame ref is front-left-up(FLU)
            state_controlled = beamng_interface_multi_agent.get_state("ego")
            pos_controlled = state_controlled[
                :3
            ]  # example of how to get car position in world frame. All data points except for dt are 3 dimensional.
            rpy_controlled = state_controlled[3:6]
            print(f"pos: {pos_controlled} | rpy: {rpy_controlled}")
            ## if you just want position, you can also do pos = beamng_interface_multi_agent.agents["<vid>"].pos


            if camera_config["enable"]:
                color = beamng_interface_multi_agent.color
                depth = beamng_interface_multi_agent.depth
                cv2.imshow(
                    "color-depth",
                    concatenated_image=np.concatenate((color, depth), axis=1),
                )
            if lidar_config["enable"]:
                if lidar_config["channels"] == 3:
                    N = beamng_interface_multi_agent.lidar_pts.shape[0] // 3
                    points = np.pad(
                        beamng_interface_multi_agent.lidar_pts[N : 2 * N, :], ((0, 0), (0, 4))
                    )  ## middle slice only.
                else:
                    points = np.pad(beamng_interface_multi_agent.lidar_pts, ((0, 0), (0, 4)))
                print(
                    "got {} points".format(N)
                )  ## I don't have a good way to display points without wreaking havoc on my system
            ## get robot_centric BEV (not rotated into robot frame)
            ## TODO: this could be optimized away with the utils functionality.
            ## TODO: move minimal example to the "examples" folder.
            BEV_color = beamng_interface_multi_agent.BEV_color
            BEV_heght = (beamng_interface_multi_agent.BEV_heght + Map_config["elevation_range"]) / (
                2 * Map_config["elevation_range"]
            )  #  BEV normalization
            BEV_segmt = beamng_interface_multi_agent.BEV_segmt
            BEV_path = beamng_interface_multi_agent.BEV_path  # trail/roads
            BEV_normal = beamng_interface_multi_agent.BEV_normal
            ## displaying BEV for visualization:
            BEV = cv2.resize(BEV_color, (500, 500), interpolation=cv2.INTER_AREA)
            cv2.imshow("color", BEV)
            BEV = cv2.resize(BEV_heght, (500, 500), interpolation=cv2.INTER_AREA)
            cv2.imshow("height", BEV)
            BEV = cv2.resize(
                BEV_normal[:, :, 1], (500, 500), interpolation=cv2.INTER_AREA
            )
            cv2.imshow("segment", BEV)
            cv2.waitKey(1)
     
            ## you can now "encapsulate the BEV and state into whatever form of "observation" you want.
            ## example of how to send controls:
            ## steering is 0th index, throttle/brake/reverse is 1st index. steering is +ve when turning left (following ROS REP103 convention)
            action = np.ones(
                2, dtype=np.float64
            )  # has to be numpy array. The inputs are always between (-1.0, 1.0) (for both throttle and steering)
            beamng_interface_multi_agent.send_ctrl({"auto": action})

            ## check if the car has flipped over. This can be replaced by whatever your reset condition is
            if(abs(rpy_controlled[0]) > np.pi/2 or abs(rpy_controlled[1]) > np.pi/2):
                beamng_interface_multi_agent.reset(["ego"])

        except Exception:
            print(traceback.format_exc())
    beamng_interface_multi_agent.bng.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--remote",
        type=bool,
        default=True,
        help="whether to connect to a remote beamng server",
    )
    parser.add_argument(
        "--host_IP",
        type=str,
        default="169.254.216.9",
        help="host ip address if using remote beamng",
    )
    parser.add_argument(
        "--map_name",
        type=str,
        default="small_island",
        help="name of map to load"
    )
    args = parser.parse_args()

    main(args)
