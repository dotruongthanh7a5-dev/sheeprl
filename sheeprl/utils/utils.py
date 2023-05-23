import os
from typing import Optional, Tuple

import gymnasium as gym
import torch
from torch import Tensor
from torch.utils._device import _device_constructors
from sheeprl.envs.wrappers import MaskVelocityWrapper


@torch.no_grad()
def gae(
    rewards: Tensor,
    values: Tensor,
    dones: Tensor,
    next_value: Tensor,
    next_done: Tensor,
    num_steps: int,
    gamma: float,
    gae_lambda: float,
) -> Tuple[Tensor, Tensor]:
    """Compute returns and advantages following https://arxiv.org/abs/1506.02438

    Args:
        rewards (Tensor): all rewards collected from the last rollout
        values (Tensor): all values collected from the last rollout
        dones (Tensor): all dones collected from the last rollout
        next_value (Tensor): next observation
        next_done (Tensor): next done
        num_steps (int): the number of steps played
        gamma (float): discout factor
        gae_lambda (float): lambda for GAE estimation

    Returns:
        estimated returns
        estimated advantages
    """
    advantages = torch.zeros_like(rewards)
    lastgaelam = 0
    not_done = torch.logical_not(dones)
    for t in reversed(range(num_steps)):
        if t == num_steps - 1:
            nextnonterminal = torch.logical_not(next_done)
            nextvalues = next_value
        else:
            nextnonterminal = not_done[t + 1]
            nextvalues = values[t + 1]
        delta = rewards[t] + gamma * nextvalues * nextnonterminal - values[t]
        advantages[t] = lastgaelam = delta + gamma * gae_lambda * nextnonterminal * lastgaelam
    returns = advantages + values
    return returns, advantages


@torch.no_grad()
def normalize_tensor(tensor: Tensor, eps: float = 1e-8, mask: Optional[Tensor] = None):
    if mask is None:
        mask = torch.ones_like(tensor, dtype=torch.bool)
    return (tensor - tensor[mask].mean()) / (tensor[mask].std() + eps)


def polynomial_decay(
    current_step: int,
    *,
    initial: float = 1.0,
    final: float = 0.0,
    max_decay_steps: int = 100,
    power: float = 1.0,
) -> float:
    if current_step > max_decay_steps or initial == final:
        return final
    else:
        return (initial - final) * ((1 - current_step / max_decay_steps) ** power) + final


def make_env(
    env_id: str,
    seed: Optional[int],
    idx: int,
    capture_video: bool,
    run_name: Optional[str] = None,
    prefix: str = "",
    mask_velocities: bool = False,
    vector_env_idx: int = 0,
):
    def thunk():
        env = gym.make(env_id, render_mode="rgb_array")
        if mask_velocities:
            env = MaskVelocityWrapper(env)
        env = gym.wrappers.RecordEpisodeStatistics(env)
        if capture_video:
            if vector_env_idx == 0 and idx == 0 and run_name is not None:
                env = gym.experimental.wrappers.RecordVideoV0(
                    env,
                    os.path.join(run_name, prefix + "_videos" if prefix else "videos"),
                    disable_logger=True,
                )
        env.action_space.seed(seed)
        env.observation_space.seed(seed)
        return env

    return thunk


# Taken from https://github.com/Lightning-AI/lit-parrot/blob/main/lit_parrot/utils.py
class EmptyInitOnDevice(torch.overrides.TorchFunctionMode):
    def __init__(self, device=None, dtype=None):
        """
        Create tensors with given device and dtype and don't run initialization
           (but instead use "empty tensors", i.e. uninitialized memory).

            device: `torch.device` to work with
            dtype: `torch.dtype` to work with

        Example::
            with EmptyInitOnDevice("cuda", dtype=torch.bfloat16):
               model = ...
            model.load_state_dict(torch.load('checkpoint.pth'))
        """
        self.device = device
        self.dtype = dtype

    def __enter__(self):
        return super().__enter__()

    def __exit__(self, exc_type, exc_val, exc_tb):
        return super().__exit__(exc_type, exc_val, exc_tb)

    def __torch_function__(self, func, types, args=(), kwargs=None):
        kwargs = kwargs or {}
        if getattr(func, "__module__", None) == "torch.nn.init":
            if "tensor" in kwargs:
                return kwargs["tensor"]
            else:
                return args[0]
        if self.device is not None and func in _device_constructors() and kwargs.get("device") is None:
            kwargs["device"] = self.device
        if self.dtype is not None and func in _device_constructors() and kwargs.get("dtype") is None:
            kwargs["dtype"] = self.dtype
        return func(*args, **kwargs)
