import os
import sys
import numpy as np
import pandas as pd
import tqdm
from multiprocessing import Pool
import matplotlib
import matplotlib.pyplot as plt
from utils import plotting
from utils import env
from tracking import LocalTrackingController, CollisionError

# Use a non-interactive backend
matplotlib.use('Agg')

# Suppress print statements
class SuppressPrints:
    def __enter__(self):
        self._original_stdout = sys.stdout
        sys.stdout = open(os.devnull, 'w')

    def __exit__(self, exc_type, exc_val, exc_tb):
        sys.stdout.close()
        sys.stdout = self._original_stdout

def single_agent_simulation(distance, velocity, theta, gamma1, gamma2, deadlock_threshold=0.1, max_sim_time=5):
    try:
        dt = 0.05

        # Define waypoints and unknown obstacles based on sampled parameters
        waypoints = np.array([
            [1, 3, theta+0.01, velocity],
            [11, 3, 0, 0]
        ], dtype=np.float64)

        x_init = waypoints[0]
        x_goal = waypoints[-1]

        env_handler = env.Env(width=12.0, height=6.0)
        plot_handler = plotting.Plotting(env_handler)
        ax, fig = plot_handler.plot_grid("Local Tracking Controller")

        tracking_controller = LocalTrackingController(
            x_init, type='DynamicUnicycle2D', dt=dt,
            show_animation=False, save_animation=False,
            ax=ax, fig=fig, env=env_handler,
            waypoints=waypoints, data_generation=True
        )

        # Set gamma values
        tracking_controller.gamma1 = gamma1
        tracking_controller.gamma2 = gamma2
        
        # Set unknown obstacles
        unknown_obs = np.array([[1 + distance, 3, 0.1]])
        tracking_controller.set_unknown_obs(unknown_obs)

        tracking_controller.set_waypoints(waypoints)
        tracking_controller.set_init_state()

        # Run simulation
        unexpected_beh = 0
        deadlock_time = 0.0
        sim_time = 0.0
        safety_loss = 0.0

        for _ in range(int(max_sim_time / dt)):
            try:
                ret = tracking_controller.control_step()
                unexpected_beh += ret
                sim_time += dt

                # Check for deadlock
                if np.abs(tracking_controller.robot.X[3]) < deadlock_threshold:
                    deadlock_time += dt

                # Store max safety metric
                if tracking_controller.safety_loss > safety_loss:
                    safety_loss = tracking_controller.safety_loss[0]

            # If collision occurs, handle the exception
            except CollisionError:
                plt.close(fig)
                return distance, velocity, theta, gamma1, gamma2, False, safety_loss, deadlock_time, sim_time

        plt.close(fig)
        return distance, velocity, theta, gamma1, gamma2, True, safety_loss, deadlock_time, sim_time

    except CollisionError:
        plt.close(fig)
        return distance, velocity, theta, gamma1, gamma2, False, safety_loss, deadlock_time, sim_time

def worker(params):
    distance, velocity, theta, gamma1, gamma2 = params
    with SuppressPrints():
        result = single_agent_simulation(distance, velocity, theta, gamma1, gamma2)
    return result

def generate_data(samples_per_dimension=5, num_processes=8, batch_size=6):
    distance_range = np.linspace(0.35, 3.0, samples_per_dimension)
    velocity_range = np.linspace(0.01, 1.0, samples_per_dimension)
    theta_range = np.linspace(0.001, np.pi / 2, samples_per_dimension)
    gamma1_range = np.linspace(0.005, 0.99, samples_per_dimension)
    gamma2_range = np.linspace(0.005, 0.99, samples_per_dimension)

    parameter_space = [(d, v, theta, g1, g2) for d in distance_range
                       for v in velocity_range
                       for theta in theta_range
                       for g1 in gamma1_range
                       for g2 in gamma2_range]

    total_batches = len(parameter_space) // batch_size + (1 if len(parameter_space) % batch_size != 0 else 0)

    for batch_index in range(total_batches):
        batch_parameters = parameter_space[batch_index * batch_size:(batch_index + 1) * batch_size]

        pool = Pool(processes=num_processes)
        results = []
        for result in tqdm.tqdm(pool.imap(worker, batch_parameters), total=len(batch_parameters)):
            results.append(result)
        pool.close()
        pool.join()

        df = pd.DataFrame(results, columns=['Distance', 'Velocity', 'Theta', 'Gamma1', 'Gamma2', 'No Collision', 'Safety Loss', 'Deadlock Time', 'Simulation Time'])
        df.to_csv(f'data_generation_results_batch_{batch_index + 1}.csv', index=False)

def concatenate_csv_files(output_filename, total_batches):
    all_data = []

    for batch_index in range(total_batches):
        batch_file = f'data_generation_results_batch_{batch_index + 1}.csv'
        batch_data = pd.read_csv(batch_file)
        all_data.append(batch_data)

    final_df = pd.concat(all_data, ignore_index=True)
    final_df.to_csv(output_filename, index=False)
    print(f"All batch files have been concatenated into {output_filename}")




if __name__ == "__main__":
    samples_per_dimension = 10  # Number of samples per dimension
    batch_size = 7**5           # Specify the batch size
    num_processes = 8           # Change based on the number of cores available

    total_datapoints = samples_per_dimension ** 5
    total_batches = total_datapoints // batch_size + (1 if total_datapoints % batch_size != 0 else 0)
    
    generate_data(samples_per_dimension, num_processes, batch_size)
    concatenate_csv_files(f'data_generation_results_{samples_per_dimension}datapoint.csv', total_batches)

    print("Data generation complete.")