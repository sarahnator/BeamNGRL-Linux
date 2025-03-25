import gym
from gym.envs.registration import register
from BeamNGRL.gym.envs import OffroadSmallIsland, OffroadUtah

register(
    id='offroad-small-island-v0',
    entry_point='BeamNGRL.gym.envs:OffroadSmallIsland',
    kwargs={}
)
register(
    id='offroad-utah-v0',
    entry_point='BeamNGRL.gym.envs:OffroadUtah',
    kwargs={}
)