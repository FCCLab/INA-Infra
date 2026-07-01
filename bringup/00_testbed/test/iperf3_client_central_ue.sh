#!/bin/sh

iperf3 -c 10.1.137.10 --bind-dev br-int-ue -t 0 -P 20
