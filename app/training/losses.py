"""Multi-task loss with uncertainty weighting.

Combines classification (direction), regression (relative high/low/close), and
volatility regression. Task weights are learned via Kendall et al. (2018)
homoscedastic uncertainty weighting so we don't hand-tune loss coefficients.
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F


class MultiTaskLoss(nn.Module):
    def __init__(self):
        super().__init__()
        # log(sigma^2) per task; initialised to 0 -> weight 1.
        self.log_var = nn.Parameter(torch.zeros(3))

    def forward(self, outputs: dict[str, torch.Tensor], targets: dict[str, torch.Tensor]):
        l_dir = F.cross_entropy(outputs["direction_logits"], targets["direction"])
        l_price = F.smooth_l1_loss(outputs["prices"], targets["prices"])
        l_vol = F.smooth_l1_loss(outputs["volatility"], targets["volatility"])

        losses = torch.stack([l_dir, l_price, l_vol])
        precision = torch.exp(-self.log_var)
        weighted = (precision * losses + self.log_var).sum()
        components = {
            "loss": weighted,
            "loss_direction": l_dir.detach(),
            "loss_price": l_price.detach(),
            "loss_volatility": l_vol.detach(),
        }
        return weighted, components
