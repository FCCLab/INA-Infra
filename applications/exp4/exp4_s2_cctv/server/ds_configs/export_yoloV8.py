#!/usr/bin/env python3
"""YOLOv8 → ONNX exporter for DeepStream-Yolo (vendored from marcoslucianops/DeepStream-Yolo)."""

from __future__ import annotations

import argparse
import os
import sys
from copy import deepcopy

import onnx
import torch
import torch.nn as nn
from ultralytics import YOLO
from ultralytics.nn.modules import C2f, Detect, v10Detect
import ultralytics.models.yolo
import ultralytics.utils
import ultralytics.utils.tal as _m

sys.modules["ultralytics.yolo"] = ultralytics.models.yolo
sys.modules["ultralytics.yolo.utils"] = ultralytics.utils


def _dist2bbox(distance, anchor_points, xywh=False, dim=-1):
    lt, rb = distance.chunk(2, dim)
    x1y1 = anchor_points - lt
    x2y2 = anchor_points + rb
    return torch.cat((x1y1, x2y2), dim)


_m.dist2bbox.__code__ = _dist2bbox.__code__


class DeepStreamOutput(nn.Module):
    def forward(self, x):
        x = x.transpose(1, 2)
        boxes = x[:, :, :4]
        scores, labels = torch.max(x[:, :, 4:], dim=-1, keepdim=True)
        return torch.cat([boxes, scores, labels.to(boxes.dtype)], dim=-1)


def yolov8_export(weights, device, fuse=True):
    model = YOLO(weights)
    model = deepcopy(model.model).to(device)
    for p in model.parameters():
        p.requires_grad = False
    model.eval()
    model.float()
    if fuse:
        model = model.fuse()
    for _k, m in model.named_modules():
        if isinstance(m, (Detect, v10Detect)):
            m.dynamic = False
            m.export = True
            m.format = "onnx"
        elif isinstance(m, C2f):
            m.forward = m.forward_split
    return model


def main(args):
    device = torch.device("cpu")
    model = yolov8_export(args.weights, device)
    if len(model.names.keys()) > 0:
        with open("labels.txt", "w", encoding="utf-8") as f:
            for name in model.names.values():
                f.write(f"{name}\n")
    model = nn.Sequential(model, DeepStreamOutput())
    img_size = args.size * 2 if len(args.size) == 1 else args.size
    onnx_input_im = torch.zeros(args.batch, 3, *img_size).to(device)
    onnx_output_file = args.weights.rsplit(".", 1)[0] + ".onnx"
    dynamic_axes = {"input": {0: "batch"}, "output": {0: "batch"}} if args.dynamic else None
    torch.onnx.export(
        model,
        onnx_input_im,
        onnx_output_file,
        verbose=False,
        opset_version=args.opset,
        do_constant_folding=True,
        input_names=["input"],
        output_names=["output"],
        dynamic_axes=dynamic_axes,
    )
    if args.simplify:
        import onnxslim

        model_onnx = onnxslim.slim(onnx.load(onnx_output_file))
        onnx.save(model_onnx, onnx_output_file)
    print(f"Done: {onnx_output_file}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("-w", "--weights", required=True)
    p.add_argument("-s", "--size", nargs="+", type=int, default=[640])
    p.add_argument("--opset", type=int, default=17)
    p.add_argument("--simplify", action="store_true")
    p.add_argument("--dynamic", action="store_true")
    p.add_argument("--batch", type=int, default=1)
    args = p.parse_args()
    if not os.path.isfile(args.weights):
        raise SystemExit(f"missing weights: {args.weights}")
    if args.dynamic and args.batch > 1:
        raise SystemExit("cannot set --dynamic and --batch>1 together")
    main(args)
