#!/bin/sh

iperf3 -c 10.1.137.10 --bind-dev br-int-edge -t 0 -P 10 -u -b 500M
