from gymnasium.envs.registration import register

register(
    id="resource_allocater_env/GridWorld-v0",
    entry_point="resource_allocater_env.envs:GridWorldEnv",
)
