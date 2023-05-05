import numpy as np
import cv2
from BeamNGRL.BeamNG.beamng_interface import *
import traceback
import torch
from BeamNGRL.control.UW_mppi.MPPI import MPPI
from BeamNGRL.control.UW_mppi.Dynamics.SimpleCarDynamics import SimpleCarDynamics
from BeamNGRL.control.UW_mppi.Costs.SimpleCarCost import SimpleCarCost
from BeamNGRL.utils.visualisation import costmap_vis
from BeamNGRL.utils.planning import update_goal


def main(map_name, start_pos, start_quat, BeamNG_dir="/home/stark/", target_WP=None):
    map_res = 0.25
    map_size = 32  # 16 x 16 map
    speed_max = 20
    dt = 0.02
    BW = 2.0

    with torch.no_grad():
        ## BEGIN MPPI
        dtype = torch.float
        d = torch.device("cuda")

        ## potentially these things should be loaded in from some config file? Will torchscript work with that?
        dynamics = SimpleCarDynamics(
            wheelbase=2.6,
            speed_max=speed_max,
            steering_max=0.6,
            dt=dt,
            BEVmap_size=map_size,
            BEVmap_res=map_res,
            ROLLOUTS=1024,
            TIMESTEPS=48,
            BINS=1,
            BW=BW
        )
        costs = SimpleCarCost(
            goal_w=0.5,
            speed_w=1,
            roll_w=2, ## weight on roll index, but also controls for lateral acceleration limits.. something to think about is how longitudenal accel affects accel limits..
            lethal_w=4, # weight on lethal stuff. Note that this is applied to a piecewise function which is = 1/cos(surface angle) for SA < thresh and 1000 for SA > thresh
            speed_target=10, ## target speed in m/s
            critical_SA=1/np.cos(0.3), # 0.5 is the critical slope angle, 1/cos(angle) is used for state cost evaluation
            critical_RI=0.2, ## limiting ratio of lateral to vertical acceleration
            BEVmap_size=map_size,
            BEVmap_res=map_res,
        )
        # dyn = torch.jit.script(dynamics)
        # dyn.save("dynamics.pt")
        # cst = torch.jit.script(costs)
        # cst.save("costs.pt")

        ns = torch.zeros((2, 2), device=d, dtype=dtype)
        ns[0, 0] = 1.0  # steering
        ns[1, 1] = 1.0  # throttle/brake

        controller = MPPI(
            dynamics,
            costs,
            CTRL_NOISE=ns,
            lambda_=0.02,
        )

        # controller = torch.jit.script(controller)
        # controller.eval()
        ## END MPPI
        bng_interface = get_beamng_default(
            car_model='RACER',
            start_pos=start_pos,
            start_quat=start_quat,
            map_name=map_name,
            car_make='sunburst',
            beamng_path=BNG_HOME,
            map_res=map_res,
            map_size=map_size
        )
        bng_interface.set_lockstep(True)
        
        current_wp_index = 0  # initialize waypoint index with 0
        goal = None
        action = np.zeros(2)

        while True:
            try:
                bng_interface.state_poll()
                now = time.time()
                # state is np.hstack((pos, rpy, vel, A, G, st, th/br)) ## note that velocity is in the body-frame
                state = np.copy(bng_interface.state)
                # state = np.zeros(17)
                pos = np.copy(
                    state[:2]
                )  # example of how to get car position in world frame. All data points except for dt are 3 dimensional.
                goal, terminate, current_wp_index = update_goal(
                    goal, pos, target_WP, current_wp_index, 15
                )

                if terminate:
                    print("done!")
                    bng_interface.send_ctrl(np.zeros(2))
                    time.sleep(5)
                    exit()
                ## get robot_centric BEV (not rotated into robot frame)
                BEV_heght = torch.from_numpy(bng_interface.BEV_heght).to(device=d, dtype=dtype)
                BEV_normal = torch.from_numpy(bng_interface.BEV_normal).to(device=d, dtype=dtype)
                BEV_center = torch.from_numpy(state[:3]).to(device=d, dtype=dtype)
                BEV_path = torch.from_numpy(bng_interface.BEV_path).to(device=d, dtype=dtype)/255
                
                
                BEV_color = bng_interface.BEV_color # this is just for visualization

                controller.Dynamics.set_BEV(BEV_heght, BEV_normal, BEV_center)
                controller.Costs.set_BEV(BEV_heght, BEV_normal, BEV_path)
                controller.Costs.set_goal(
                    torch.from_numpy(np.copy(goal) - np.copy(pos)).to(device=d, dtype=dtype)
                )  # you can also do this asynchronously

                state[:3] = np.zeros(3) # this is for the MPPI: technically this should be state[:3] -= BEV_center

                # we use our previous control output as input for next cycle!
                state[15:17] = action ## adhoc wheelspeed.
                delta_action = np.array(
                    controller.forward(
                        torch.from_numpy(state).to(device=d, dtype=dtype)
                    )
                    .cpu()
                    .numpy(),
                    dtype=np.float64,
                )[0] * dt * BW
                action += delta_action
                action = np.clip(action, -1, 1)
                action[1] = np.clip(action[1], 0, 0.5)
                dt_ = time.time() - now
                
                costmap_vis(
                    controller.Dynamics.states.cpu().numpy(),
                    pos,
                    np.copy(goal),
                    # 1/bng_interface.BEV_normal[:,:,2]*0.1,
                    BEV_path.cpu().numpy(),
                    1 / map_res,
                )
                bng_interface.send_ctrl(action, speed_ctrl=True, speed_max = 20, Kp=1, Ki=0.05, Kd=0.0, FF_gain=0.0)

            except Exception:
                print(traceback.format_exc())

        # bng_interface.bng.close()


if __name__ == "__main__":
    # position of the vehicle for tripped_flat on grimap_v2
    start_point = np.array([-67, 336, 0.5])
    start_quat = np.array([0, 0, 0.3826834, 0.9238795])
    map_name = "smallgrid"
    target_WP = np.load("WP_file_offroad.npy")
    main(map_name, start_point, start_quat, target_WP=target_WP)
