import numpy as np
from .dataset_utils import to_np, project_traj_to_map
from PIL import Image
import cv2


def make_heatmap(bev_map, cmap='inferno'):
    import matplotlib.cm
    cmap = matplotlib.cm.get_cmap(cmap)
    bev_map = bev_map / 255.0
    color = cmap(bev_map)[:, :, :3] * 255.0
    ret = color.astype(np.uint8)
    return ret


def project_traj_to_img(traj, img, color=(255, 0, 0), type=None, size=2):
    for i in range(traj.shape[0]):
        if type == 'circle':
            cv2.circle(img, tuple(traj[i, :2]), size, color, -1, cv2.LINE_AA)
        elif type =='square':
            tl = traj[i, :2] + np.array([-size, size])
            br = traj[i, :2] + np.array([size, -size])
            cv2.rectangle(img, tl, br, color, -1)
        else:
            x, y = traj[i, :2]
            img[y, x, :] = color
    return img


def visualize_bev_traj(state, future_traj, past_traj, bev_map, resolution):

    state = to_np(state)
    future_traj = to_np(future_traj)
    past_traj = to_np(past_traj)
    bev_map = to_np(bev_map)

    height, width, n_channels = bev_map.shape
    if n_channels == 3:
        bev_img = bev_map.astype(np.uint8)
    elif n_channels == 1:
        bev_img = make_heatmap(bev_map)
    else:
        raise IOError('BEV map contains an invalid number of channels '
                      'for visualization.')

    future_traj_bev, _ = project_traj_to_map(future_traj, height, resolution)
    past_traj_bev, _ = project_traj_to_map(past_traj, height, resolution)
    state_bev, _ = project_traj_to_map(state[None, :], height, resolution)

    # Plot trajectories
    size = 5
    bev_img = project_traj_to_img(future_traj_bev, bev_img, [0, 0, 255], 'circle', size)
    bev_img = project_traj_to_img(past_traj_bev, bev_img, [255, 0, 0], 'circle', size)
    bev_img = project_traj_to_img(state_bev, bev_img, [0, 255, 0], 'circle', size)

    final_img = Image.fromarray(bev_img.astype(np.uint8))

    return final_img


def visualize_preds(
        future_traj_gt,
        future_traj_pred,
        ctx_dict,
        step_iter,
        batch_idx=0,
        mode='Train',
        writer=None,
        resolution=0.25,
):

    future_traj_gt = to_np(future_traj_gt[batch_idx])
    future_traj_pred = to_np(future_traj_pred[batch_idx])

    state = to_np(ctx_dict['state'][batch_idx])
    past_states = to_np(ctx_dict['past_states'][batch_idx])
    bev_color = to_np(ctx_dict['bev_color'][batch_idx]).transpose(1, 2, 0)
    bev_elev = to_np(ctx_dict['bev_elev'][batch_idx]).transpose(1, 2, 0)
    bev_normal = to_np(ctx_dict['bev_normal'][batch_idx]).transpose(1, 2, 0)

    grid_size, grid_size, n_channels = bev_color.shape
    # bev_color = bev_color.astype(np.uint8)
    bev_color = np.ascontiguousarray(bev_color, dtype=np.uint8)
    bev_elev = make_heatmap(bev_elev)
    bev_normal = make_heatmap(bev_normal)

    # print(f'\nbev_color shape: {bev_color.shape}')
    # print(f'{bev_color.max()}')
    # print(f'{bev_color.min()}')
    #
    # print(f'\nbev_elev shape: {bev_elev.shape}')
    # print(f'{bev_elev.max()}')
    # print(f'{bev_elev.min()}')
    #
    # print(f'\nbev_normal shape: {bev_normal.shape}')
    # print(f'{bev_normal.max()}')
    # print(f'{bev_normal.min()}')

    future_gt_bev, _ = project_traj_to_map(future_traj_gt, grid_size, resolution)
    future_pred_bev, _ = project_traj_to_map(future_traj_pred, grid_size, resolution)
    past_states_bev, _ = project_traj_to_map(past_states, grid_size, resolution)
    state_bev, _ = project_traj_to_map(state[None, :], grid_size, resolution)

    # Plot trajectories
    size = 3
    type = 'circle'
    def plot_to_bev(bev_img, future_traj_bev):
        bev_img = project_traj_to_img(future_traj_bev, bev_img, [0, 0, 255], type, size)
        bev_img = project_traj_to_img(past_states_bev, bev_img, [255, 0, 0], type, size)
        bev_img = project_traj_to_img(state_bev, bev_img, [0, 255, 0], type, size)
        return bev_img

    bev_color_gt_img = plot_to_bev(np.copy(bev_color), future_gt_bev)[None]
    bev_color_pred_img = plot_to_bev(np.copy(bev_color), future_pred_bev)[None]
    # bev_elev_img = plot_to_bev(bev_elev)[None]
    # bev_normal_img = plot_to_bev(bev_normal)[None]

    if writer is not None:
        writer.add_images(mode+f'/gt_sem_{batch_idx}', bev_color_gt_img, global_step=step_iter, dataformats='NHWC')
        writer.add_images(mode+f'/pred_sem_{batch_idx}', bev_color_pred_img, global_step=step_iter, dataformats='NHWC')
        # writer.add_images(mode+f'/elev_{batch_idx}', bev_elev_img, global_step=step_iter, dataformats='NHWC')
        # writer.add_images(mode+f'/normal_{batch_idx}', bev_normal_img, global_step=step_iter, dataformats='NHWC')



