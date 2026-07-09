"""Hybrid deep model for multi-task market prediction.

Pipeline:  Input(T,F)
             │  causal Temporal Convolution stack (dilated, residual)
             ▼
           Transformer Encoder (multi-head self-attention, N layers)
             ▼
           Residual fully-connected trunk
             ▼
           Multi-task heads:
             * direction logits  (bull / bear / sideways)
             * price deltas       (high / low / close, relative to last close)
             * expected volatility (softplus, non-negative)
             * confidence         (sigmoid, calibrated separately)

The model predicts *relative* price moves (fraction of last close), which keeps
targets scale-free and stable across symbols and price regimes.
"""

from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn as nn
import torch.nn.functional as F


@dataclass
class ModelConfig:
    n_features: int
    seq_len: int = 128
    tcn_channels: tuple[int, ...] = (64, 64, 128)
    tcn_kernel: int = 3
    d_model: int = 128
    n_heads: int = 8
    n_transformer_layers: int = 3
    ff_dim: int = 256
    dropout: float = 0.1
    fc_dim: int = 128


class _CausalConv1d(nn.Module):
    """1-D convolution with left padding so output[t] never sees input[>t]."""

    def __init__(self, in_ch: int, out_ch: int, kernel: int, dilation: int):
        super().__init__()
        self.pad = (kernel - 1) * dilation
        self.conv = nn.Conv1d(in_ch, out_ch, kernel, dilation=dilation)

    def forward(self, x: torch.Tensor) -> torch.Tensor:  # x: (B, C, T)
        x = F.pad(x, (self.pad, 0))
        return self.conv(x)


class _TCNBlock(nn.Module):
    def __init__(self, in_ch: int, out_ch: int, kernel: int, dilation: int, dropout: float):
        super().__init__()
        self.conv1 = _CausalConv1d(in_ch, out_ch, kernel, dilation)
        self.conv2 = _CausalConv1d(out_ch, out_ch, kernel, dilation)
        self.norm1 = nn.BatchNorm1d(out_ch)
        self.norm2 = nn.BatchNorm1d(out_ch)
        self.drop = nn.Dropout(dropout)
        self.down = nn.Conv1d(in_ch, out_ch, 1) if in_ch != out_ch else nn.Identity()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        residual = self.down(x)
        y = self.drop(F.gelu(self.norm1(self.conv1(x))))
        y = self.drop(F.gelu(self.norm2(self.conv2(y))))
        return F.gelu(y + residual)


class _PositionalEncoding(nn.Module):
    def __init__(self, d_model: int, max_len: int = 4096):
        super().__init__()
        pe = torch.zeros(max_len, d_model)
        pos = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
        div = torch.exp(torch.arange(0, d_model, 2).float() * (-torch.log(torch.tensor(10000.0)) / d_model))
        pe[:, 0::2] = torch.sin(pos * div)
        pe[:, 1::2] = torch.cos(pos * div)
        self.register_buffer("pe", pe.unsqueeze(0))

    def forward(self, x: torch.Tensor) -> torch.Tensor:  # x: (B, T, d)
        return x + self.pe[:, : x.size(1)]


class HybridTradingModel(nn.Module):
    """TCN → Transformer encoder → residual FC → multi-task heads."""

    def __init__(self, cfg: ModelConfig):
        super().__init__()
        self.cfg = cfg

        # --- Temporal convolution front-end ---
        blocks: list[nn.Module] = []
        in_ch = cfg.n_features
        for i, ch in enumerate(cfg.tcn_channels):
            blocks.append(_TCNBlock(in_ch, ch, cfg.tcn_kernel, dilation=2**i, dropout=cfg.dropout))
            in_ch = ch
        self.tcn = nn.Sequential(*blocks)
        self.proj = nn.Linear(in_ch, cfg.d_model)

        # --- Transformer encoder ---
        self.pos = _PositionalEncoding(cfg.d_model, max_len=cfg.seq_len + 1)
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=cfg.d_model,
            nhead=cfg.n_heads,
            dim_feedforward=cfg.ff_dim,
            dropout=cfg.dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=cfg.n_transformer_layers)

        # --- Residual FC trunk ---
        self.fc1 = nn.Linear(cfg.d_model, cfg.fc_dim)
        self.fc2 = nn.Linear(cfg.fc_dim, cfg.fc_dim)
        self.fc_norm = nn.LayerNorm(cfg.fc_dim)
        self.drop = nn.Dropout(cfg.dropout)

        # --- Multi-task heads ---
        self.head_direction = nn.Linear(cfg.fc_dim, 3)      # logits: bull/bear/side
        self.head_prices = nn.Linear(cfg.fc_dim, 3)         # rel high/low/close
        self.head_vol = nn.Linear(cfg.fc_dim, 1)            # expected volatility
        self.head_conf = nn.Linear(cfg.fc_dim, 1)           # confidence logit

    def forward(self, x: torch.Tensor) -> dict[str, torch.Tensor]:
        """``x``: (B, T, F). Returns a dict of task tensors."""
        # TCN expects (B, C, T)
        h = self.tcn(x.transpose(1, 2)).transpose(1, 2)   # (B, T, C)
        h = self.proj(h)                                   # (B, T, d_model)
        h = self.pos(h)
        h = self.encoder(h)                                # (B, T, d_model)

        pooled = h[:, -1]                                  # last-step representation
        z = F.gelu(self.fc1(pooled))
        z = self.fc_norm(z + F.gelu(self.fc2(z)))          # residual
        z = self.drop(z)

        return {
            "direction_logits": self.head_direction(z),
            "prices": self.head_prices(z),                 # relative deltas
            "volatility": F.softplus(self.head_vol(z)).squeeze(-1),
            "confidence": torch.sigmoid(self.head_conf(z)).squeeze(-1),
        }

    @torch.no_grad()
    def predict_proba(self, x: torch.Tensor) -> dict[str, torch.Tensor]:
        self.eval()
        out = self.forward(x)
        out["direction_proba"] = F.softmax(out["direction_logits"], dim=-1)
        return out

    def num_parameters(self) -> int:
        return sum(p.numel() for p in self.parameters() if p.requires_grad)
