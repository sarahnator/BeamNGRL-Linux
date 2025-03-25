import numpy as np
from BeamNGRL.BeamNG.beamng_interface_new import get_beamng_default
import traceback
import torch
from BeamNGRL.control.UW_mppi.MPPI import MPPI
from BeamNGRL.control.UW_mppi.Dynamics.SimpleCarNetworkDyn import SimpleCarNetworkDyn
from BeamNGRL.control.UW_mppi.Costs.SimpleCarCost import SimpleCarCost
from BeamNGRL.control.UW_mppi.Sampling.Delta_Sampling import Delta_Sampling
from BeamNGRL.utils.visualisation import costmap_vis
from BeamNGRL.utils.planning import update_goal
import yaml
import argparse
from datetime import datetime
from BeamNGRL import MPPI_CONFIG_PTH, DATA_PATH, ROOT_PATH, LOGS_PATH
import time
from typing import List
import gc ## import group chat?


## I used the MPPI to train a model which I then use to collect more data. makes perfect sense.

def update_npy_datafile(buffer: List, filepath):
    buff_arr = np.array(buffer)
    if filepath.is_file():
        # Append to existing data file
        data_arr = np.load(filepath, allow_pickle=True)
        data_arr = np.concatenate((data_arr, buff_arr), axis=0)
        np.save(filepath, data_arr)
    else:
        np.save(filepath, buff_arr)
    return [] # empty buffer


def collect_mppi_data(args):

    with open(MPPI_CONFIG_PTH / 'MPPI_config.yaml') as f:
        MPPI_config = yaml.safe_load(f)

    with open(MPPI_CONFIG_PTH / 'Network_Dynamics_config.yaml') as f:
        Dynamics_config = yaml.safe_load(f)

    with open(MPPI_CONFIG_PTH / 'Cost_config.yaml') as f:
        Cost_config = yaml.safe_load(f)

    with open(MPPI_CONFIG_PTH / 'Sampling_config.yaml') as f:
        Sampling_config = yaml.safe_load(f)

    with open(MPPI_CONFIG_PTH / 'Map_config.yaml') as f:
        Map_config = yaml.safe_load(f)

    # target_WP = np.load(ROOT_PATH.parent / 'examples' / "WP_file_offroad.npy")
    # waypoints = np.load(ROOT_PATH / 'utils' / 'waypoint_files' / "WP_file_offroad.npy")
    waypoints = np.load(ROOT_PATH / 'Experiments' / 'Waypoints' / "WP_file_offroad.npy")
    target_WP = waypoints.copy()
    num_points = len(target_WP)
    target_WP = target_WP[::-1,...]

    map_res = Map_config["map_res"]
    dtype = torch.float
    device = torch.device("cuda")

    output_dir = args.output_dir
    if output_dir is None:
        date_time = datetime.now().strftime("%m_%d_%Y")
        output_dir = f'{args.map_name}_{date_time}'

    output_path = DATA_PATH / 'mppi_data' / output_dir
    output_path.mkdir(parents=True, exist_ok=True)

    # Set random seed
    torch.manual_seed(args.seed)
    np.random.rand(args.seed)

    # model_weights_path = LOGS_PATH / 'small_grid' / 'best_201.pth'
    # model_weights_path = LOGS_PATH / 'small_grid_residual' / 'epoch_300.pth'
    model_weights_path = LOGS_PATH / 'small_grid_recurrent' / 'best_46.pth'
    # model_weights_path = LOGS_PATH / 'small_grid_mlp' / 'epoch_1000.pth'

    print(model_weights_path)

    Cost_config["goal_w"] = 1.0
    Cost_config["speed_target"] = 5.0 ## go speed racer
    Cost_config["roll_w"] = 0.0
    Sampling_config["min_thr"] = -0.5

    with torch.no_grad():

        dynamics = SimpleCarNetworkDyn(
            Dynamics_config, Map_config, MPPI_config,
            model_weights_path=model_weights_path,
            device=device,
        )

        costs = SimpleCarCost(Cost_config, Map_config, device=device)
        sampling = Delta_Sampling(Sampling_config, MPPI_config, device=device)

        controller = MPPI(
            dynamics,
            costs,
            sampling,
            MPPI_config,
            device,
        )

        bng = get_beamng_default(
            car_model='offroad',
            start_pos=np.array(args.start_pos),
            start_quat=np.array(args.start_quat),
            map_name=args.map_name,
            car_make='sunburst',
            map_res=Map_config["map_res"],
            map_size=Map_config["map_size"]
        )
        bng.set_lockstep(True)
        
        current_wp_index = 0  # initialize waypoint index with 0
        goal = None
        action = np.zeros(2)

        timestamps = []
        state_data = []
        color_data = []
        elev_data = []
        segmt_data = []
        path_data = []
        normal_data = []
        reset_data = []
        reset_counter = 0
        reset_limit = 100

        start = None
        running = True
        save_prompt_time = float(args.save_every_n_sec)

        while running:
            try:
                bng.state_poll()
                state = bng.state
                ts = bng.timestamp

                if not start:
                    start = ts

                T = ts - start

                goal_distance = 10 + 7.5*np.sin(2*np.pi*20*T/args.duration)

                pos = np.copy(state[:2])  # example of how to get car position in world frame. All data points except for dt are 3 dimensional.
                goal, terminate, current_wp_index = update_goal(
                    goal, pos, target_WP, current_wp_index, goal_distance
                )
                ## reset when close to end.
                if(current_wp_index > num_points - 10):
                    current_wp_index = 0

                if terminate:
                    print("done!")
                    bng.send_ctrl(np.zeros(2))
                    time.sleep(5)
                    exit()

                # get robot_centric BEV (not rotated into robot frame)
                BEV_color = bng.BEV_color
                BEV_height = bng.BEV_heght
                BEV_segmt = bng.BEV_segmt
                BEV_path  = bng.BEV_path  # trail/roads
                BEV_normal  = bng.BEV_normal  # trail/roads

                ## get robot_centric BEV (not rotated into robot frame)
                BEV_height_tn = torch.from_numpy(BEV_height).to(device=device, dtype=dtype)
                BEV_normal_tn = torch.from_numpy(BEV_normal).to(device=device, dtype=dtype)
                BEV_path_tn = torch.from_numpy(bng.BEV_path).to(device=device, dtype=dtype)/255

                controller.Dynamics.set_BEV(BEV_height_tn, BEV_normal_tn)
                controller.Costs.set_BEV(BEV_height_tn, BEV_normal_tn, BEV_path_tn)
                controller.Costs.set_goal(
                    torch.from_numpy(np.copy(goal) - np.copy(pos)).to(device=device, dtype=dtype)
                )  # you can also do this asynchronously

                state_to_ctrl = state.copy()
                state_to_ctrl[:3] = np.zeros(3) # this is for the MPPI: technically this should be state[:3] -= BEV_center

                # we use our previous control output as input for next cycle!
                state_to_ctrl[15:17] = action ## adhoc wheelspeed.
                action = np.array(
                    controller.forward(
                        torch.from_numpy(state_to_ctrl).to(device=device, dtype=dtype)
                    )
                    .cpu()
                    .numpy(),
                    dtype=np.float64,
                )[0]
                action[1] = np.clip(action[1], Sampling_config["min_thr"], Sampling_config["max_thr"])

                costmap_vis(
                    controller.Dynamics.states.cpu().numpy(),
                    pos,
                    np.copy(goal),
                    # 1/bng.BEV_normal[:,:,2]*0.1,
                    BEV_path.copy(),
                    1 / map_res,
                )

                damage = False
                if(type(bng.broken) == dict ):
                    count = 0
                    for part in bng.broken.values():
                        if part['damage'] > 0.8:
                            count += 1
                    damage = count > 1
                reset = False
                if(damage or bng.flipped_over):
                    reset_counter += 1
                    if reset_counter >= reset_limit:
                        reset = True
                        reset_counter = 0
                        bng.reset()
                        target_WP = target_WP[::-1, ...]
                        current_wp_index = 0
                        goal = target_WP[0]
                        goal = goal[:2]
                
                # Aggregate Data
                timestamps.append(ts)
                state_data.append(state)
                # color_data.append(BEV_color)
                # elev_data.append(BEV_height)
                # segmt_data.append(BEV_segmt)
                # path_data.append(BEV_path)
                # normal_data.append(BEV_normal)
                reset_data.append(reset)

                if ts >= save_prompt_time or \
                    ts - start > args.duration:

                    print("\nSaving data...")
                    print(f"time: {ts}")
                    timestamps = update_npy_datafile(timestamps, output_path / "timestamps.npy")
                    state_data = update_npy_datafile(state_data, output_path / "state.npy")
                    # path_data = update_npy_datafile(path_data, output_path / "bev_path.npy")
                    # color_data = update_npy_datafile(color_data, output_path / "bev_color.npy")
                    # segmt_data = update_npy_datafile(segmt_data, output_path / "bev_segmt.npy")
                    # elev_data = update_npy_datafile(elev_data, output_path / "bev_elev.npy")
                    # normal_data = update_npy_datafile(normal_data, output_path / "bev_normal.npy")
                    reset_data = update_npy_datafile(reset_data, output_path / "reset.npy")

                    gc.collect()
                    save_prompt_time += float(args.save_every_n_sec)

                if ts - start > args.duration:
                    break

                bng.send_ctrl(
                    action,
                    speed_ctrl=True,
                    speed_max=20,
                    Kp=1.5,
                    Ki=0.05,
                    Kd=0.0,
                    FF_gain=0.0,
                )

            except Exception:
                print(traceback.format_exc())

        bng.bng.close()


if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument('--output_dir', type=str, default=None, help='location to store test results')
    parser.add_argument('--start_pos', type=float, default=[-67, 336, 34.5], nargs=3, help='Starting position of the vehicle for tripped_flat on grimap_v2')
    parser.add_argument('--start_quat', type=float, default=[0, 0, 0.3826834, 0.9238795], nargs=4, help='Starting rotation (quat) of the vehicle.')
    parser.add_argument('--map_name', type=str, default='small_island', help='Map name.')
    parser.add_argument('--waypoint_file', type=str, default='WP_file_offroad.npy', help='Map name.')
    parser.add_argument('--duration', type=int, default=120)
    parser.add_argument('--save_every_n_sec', type=int, default=15)
    parser.add_argument('--seed', type=int, default=0)
    args = parser.parse_args()

    collect_mppi_data(args)
