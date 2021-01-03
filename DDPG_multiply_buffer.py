#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Dec 29 18:35:01 2020
@author: wx100059
Description: Implementing DDPG algorithm on the Multiple timescale problem.
"""

import gym
# solve the multi registration bug
env_dict = gym.envs.registration.registry.env_specs.copy()
for env in env_dict:
    if 'multiple_gym-v0' in env:
        print("Remove {} from registry".format(env))
        del gym.envs.registration.registry.env_specs[env]
    if 'multiple_gym_extend-v0' in env:
        print("Remove {} from registry".format(env))
        del gym.envs.registration.registry.env_specs[env]
        
import multiple_gym   
import tensorflow as tf
from tensorflow.keras import layers
import numpy as np
import matplotlib.pyplot as plt
import replay_buffers

"""
To implement better exploration by the Actor network, we use noisy perturbations,
specifically
an **Ornstein-Uhlenbeck process** for generating noise, as described in the paper.
It samples noise from a correlated normal distribution.
"""

class OUActionNoise:
    def __init__(self, mean, std_deviation, theta=0.15, dt=1e-2, x_initial=None):
        self.theta = theta
        self.mean = mean
        self.std_dev = std_deviation
        self.dt = dt
        self.x_initial = x_initial
        self.reset()

    def __call__(self):
        # Formula taken from https://www.wikipedia.org/wiki/Ornstein-Uhlenbeck_process.
        x = (
            self.x_prev
            + self.theta * (self.mean - self.x_prev) * self.dt
            + self.std_dev * np.sqrt(self.dt) * np.random.normal(size=self.mean.shape)
        )
        # Store x into x_prev
        # Makes next noise dependent on current one
        self.x_prev = x
        return x

    def reset(self):
        if self.x_initial is not None:
            self.x_prev = self.x_initial
        else:
            self.x_prev = np.zeros_like(self.mean)


"""
The `Buffer` class implements Experience Replay.
---
![Algorithm](https://i.imgur.com/mS6iGyJ.jpg)
---
**Critic loss** - Mean Squared Error of `y - Q(s, a)`
where `y` is the expected return as seen by the Target network,
and `Q(s, a)` is action value predicted by the Critic network. `y` is a moving target
that the critic model tries to achieve; we make this target
stable by updating the Target model slowly.
**Actor loss** - This is computed using the mean of the value given by the Critic network
for the actions taken by the Actor network. We seek to maximize this quantity.
Hence we update the Actor network so that it produces actions that get
the maximum predicted value as seen by the Critic, for a given state.
"""


class Buffer:
    def __init__(self, buffer_capacity=100000, batch_size=64, num_states = 3, num_actions = 1):
        # Number of "experiences" to store at max
        self.buffer_capacity = buffer_capacity
        # Num of tuples to train on.
        self.batch_size = batch_size

        # Its tells us num of times record() was called.
        self.buffer_counter = 0

        # Dimensioin of the state  and observation
        self.num_states = num_states
        
        # Dimension of the action
        self.num_actions = num_actions
        
        # Instead of list of tuples as the exp.replay concept go
        # We use different np.arrays for each tuple element
        self.state_buffer = np.zeros((self.buffer_capacity, self.num_states))
        self.action_buffer = np.zeros((self.buffer_capacity, self.num_actions))
        self.reward_buffer = np.zeros((self.buffer_capacity, 1))
        self.next_state_buffer = np.zeros((self.buffer_capacity, self.num_states))

    # Takes (s,a,r,s') obervation tuple as input
    def record(self, obs_tuple):
        # Set index to zero if buffer_capacity is exceeded,
        # replacing old records
        index = self.buffer_counter % self.buffer_capacity

        self.state_buffer[index] = obs_tuple[0]
        self.action_buffer[index] = obs_tuple[1]
        self.reward_buffer[index] = obs_tuple[2]
        self.next_state_buffer[index] = obs_tuple[3]

        self.buffer_counter += 1

"""
## Training hyperparameters
"""
class DDPG_controller:
    def __init__(self,  upper_bound = 2, lower_bound = -2, std_dev = 0.2, critic_lr = 0.001, actor_lr = 0.0001,
                 total_episodes = 1, gamma = 1, tau = 0.001,
                 buffer_size = 1000000, batch_size = 64, num_states = 2, 
                 num_actions = 1):
        self.std_dev = std_dev
        self.ou_noise = OUActionNoise(mean=np.zeros(1), std_deviation=float(self.std_dev) * np.ones(1))
        
        self.total_episodes = total_episodes
        # Discount factor for future rewards
        self.gamma = gamma
        
        # Used to update target networks
        self.tau = tau
        
        self.buffer = Buffer(buffer_size, batch_size, num_states, num_actions)
        #self.buffer = replay_buffers.get_replay_buffer("multi_timescale")
        # Upper bound and lower bound of the output action
        self.upper_bound = upper_bound
        self.lower_bound = lower_bound
        
        self.actor_model = self.get_actor()
        self.critic_model = self.get_critic()
        
        self.target_actor = self.get_actor()
        self.target_critic = self.get_critic()
        
        # Making the weights equal initially
        self.target_actor.set_weights(self.actor_model.get_weights())
        self.target_critic.set_weights(self.critic_model.get_weights())
        
        # Learning rate for actor-critic models
        self.critic_lr = critic_lr
        self.actor_lr = actor_lr
        
        self.critic_optimizer = tf.keras.optimizers.Adam(critic_lr)
        self.actor_optimizer = tf.keras.optimizers.Adam(actor_lr) 
        
        """
    Here we define the Actor and Critic networks. These are basic Dense models
    with `ReLU` activation.
    Note: We need the initialization for last layer of the Actor to be between
    `-0.003` and `0.003` as this prevents us from getting `1` or `-1` output values in
    the initial stages, which would squash our gradients to zero,
    as we use the `tanh` activation.
    """  
    
    def get_actor(self):
        # Initialize weights between -3e-3 and 3-e3
        # last_init = tf.random_uniform_initializer(minval=-0.003, maxval=0.003)
        last_init = tf.random_uniform_initializer
    
        inputs = layers.Input(shape=(self.buffer.num_states,))
        # out = layers.Dense(256, activation="relu")(inputs)
        out = layers.Dense(self.buffer.num_states, activation= None)(inputs)
        outputs = layers.Dense(self.buffer.num_actions, activation= None, kernel_initializer=last_init)(out)
    
        # Our upper bound is 2.0 for Pendulum.
        #outputs = outputs * upper_bound
        model = tf.keras.Model(inputs, outputs)
        return model


    def get_critic(self):
        # State as input
        state_input = layers.Input(shape=(self.buffer.num_states))
        state_out = layers.Dense(50, activation="tanh")(state_input)
        state_out = layers.Dense(25, activation="tanh")(state_out)
        # Action as input
        action_input = layers.Input(shape=(self.buffer.num_actions))
        action_out = layers.Dense(25, activation="tanh")(action_input)
    
        # Both are passed through seperate layer before concatenating
        concat = layers.Concatenate()([state_out, action_out])
        outputs = layers.Dense(1)(concat)
    
        # Outputs single value for give state-action
        model = tf.keras.Model([state_input, action_input], outputs)
    
        return model

    # This update target parameters slowly
    # Based on rate `tau`, which is much less than one.
    @tf.function
    def update_target(self, target_weights, weights, tau):
        for (a, b) in zip(target_weights, weights):
            a.assign(b * tau + a * (1 - tau))
    
    
    """
    `policy()` returns an action sampled from our Actor network plus some noise for
    exploration.
    """
    
    
    def policy(self, state, noise_object):
        sampled_actions = tf.squeeze(self.actor_model(state))
        if noise_object == None:
            noise = np.zeros(self.buffer.num_actions)
        else:
            noise = noise_object()
        # Adding noise to action
        sampled_actions = sampled_actions.numpy() + noise
    
        # We make sure action is within bounds
        #legal_action = sampled_actions
        legal_action = np.clip(sampled_actions, lower_bound, upper_bound)
    
        return [np.squeeze(legal_action)]
    
    # Eager execution is turned on by default in TensorFlow 2. Decorating with tf.function allows
    # TensorFlow to build a static graph out of the logic and computations in our function.
    # This provides a large speed up for blocks of code that contain many small TensorFlow operations such as this one.
    @tf.function
    def update(
        self, state_batch, action_batch, reward_batch, next_state_batch,
    ):
        # Training and updating Actor & Critic networks.
        # See Pseudo Code.
        with tf.GradientTape() as tape:
            target_actions = self.target_actor(next_state_batch, training=True)
            y = reward_batch + self.gamma * self.target_critic(
                [next_state_batch, target_actions], training=True
            )
            critic_value = self.critic_model([state_batch, action_batch], training=True)
            critic_loss = tf.math.reduce_mean(tf.math.square(y - critic_value))

        critic_grad = tape.gradient(critic_loss, self.critic_model.trainable_variables)
        self.critic_optimizer.apply_gradients(
            zip(critic_grad, self.critic_model.trainable_variables)
        )

        with tf.GradientTape() as tape:
            actions = self.actor_model(state_batch, training=True)
            critic_value = self.critic_model([state_batch, actions], training=True)
            # Used `-value` as we want to maximize the value given
            # by the critic for our actions
            actor_loss = -tf.math.reduce_mean(critic_value)

        actor_grad = tape.gradient(actor_loss, self.actor_model.trainable_variables)
        self.actor_optimizer.apply_gradients(
            zip(actor_grad, self.actor_model.trainable_variables)
        )
        
    # We compute the loss and update parameters
    def learn(self):
        # Get sampling range
        record_range = min(self.buffer.buffer_counter, self.buffer.buffer_capacity)
        # Randomly sample indices
        batch_indices = np.random.choice(record_range, self.buffer.batch_size)

        # Convert to tensors
        state_batch = tf.convert_to_tensor(self.buffer.state_buffer[batch_indices])
        action_batch = tf.convert_to_tensor(self.buffer.action_buffer[batch_indices])
        reward_batch = tf.convert_to_tensor(self.buffer.reward_buffer[batch_indices])
        reward_batch = tf.cast(reward_batch, dtype=tf.float64)
        next_state_batch = tf.convert_to_tensor(self.buffer.next_state_buffer[batch_indices])

        self.update(state_batch, action_batch, reward_batch, next_state_batch)

"""
We use [OpenAIGym](http://gym.openai.com/docs) to create the environment.
We will use the `upper_bound` parameter to scale our actions later.
"""

#problem = "Pendulum-v0"
problem = "multiple_gym-v0"
env = gym.make(problem)

num_states = env.observation_space.shape[0]
print("Size of State Space ->  {}".format(num_states))
num_actions = env.action_space.shape[0]
print("Size of Action Space ->  {}".format(num_actions))

# the upper_bound and lower_bound is uniform for now, in the future we will add element-specific bound.
upper_bound = env.action_space.high[0]
lower_bound = env.action_space.low[0]

print("Max Value of Action ->  {}".format(upper_bound))
print("Min Value of Action ->  {}".format(lower_bound))

# set all tensorflow layers have dtype float64
tf.keras.backend.set_floatx('float64')

# Takes about 4 min to train
total_episodes = 300

controller = DDPG_controller(total_episodes = total_episodes, buffer_size = 1000000,
                             batch_size = 64, num_states = num_states,
                             critic_lr = 0.001, actor_lr = 0.0001, 
                             num_actions = num_actions, upper_bound = upper_bound,
                             lower_bound = lower_bound, gamma = 1, tau = 0.001)
"""
Now we implement our main training loop, and iterate over episodes.
We sample actions using `policy()` and train with `learn()` at each time step,
along with updating the Target networks at a rate `tau`.
"""

# To store reward history of each episode
ep_reward_list = []
# # To store average reward history of last few episodes
avg_reward_list = []

for ep in range(controller.total_episodes):

    prev_state = env.reset()
    episodic_reward = 0
    max_state = np.zeros(controller.buffer.num_states)
    max_action = np.zeros(controller.buffer.num_actions)
    while True:
        # Uncomment this to see the Actor in action
        # But not in a python notebook.
        # env.render()
        
        tf_prev_state = tf.expand_dims(tf.convert_to_tensor(prev_state), 0)

        # action = controller.policy(tf_prev_state, controller.ou_noise) 
        action = controller.policy(tf_prev_state, noise_object = None)
        # Recieve state and reward from environment.
        state, reward, done, info = env.step(action)
        if np.linalg.norm(state)>np.linalg.norm(max_state):
            max_state = state
        if np.linalg.norm(action) > np.linalg.norm(max_action):
            max_action = action
        controller.buffer.record((prev_state, action, reward, state))
        episodic_reward += reward

        controller.learn()
        controller.update_target(controller.target_actor.variables, controller.actor_model.variables, controller.tau)
        controller.update_target(controller.target_critic.variables, controller.critic_model.variables, controller.tau)

        # End this episode when `done` is True
        if done:
            break

        prev_state = state

    ep_reward_list.append(episodic_reward)

    # Mean of last 20 episodes
    avg_reward = np.mean(ep_reward_list[-20:])
    print("Episode * {} * Avg Reward is ==> {}".format(ep, avg_reward))
    # print("Episode * {} * Max State is ==> {}".format(ep, max_action))
    # print("Episode * {} * Max State is ==> {}".format(ep, max_state))
    avg_reward_list.append(avg_reward)

# Plotting graph
# Episodes versus Avg. Rewards
plt.plot(avg_reward_list)
plt.xlabel("Episode")
plt.ylabel("Avg. Epsiodic Reward")
plt.show()


# # Save the weights
# controller.actor_model.save_weights("pendulum_actor.h5")
# controller.critic_model.save_weights("pendulum_critic.h5")

# controller.target_actor.save_weights("pendulum_target_actor.h5")
# controller.target_critic.save_weights("pendulum_target_critic.h5")