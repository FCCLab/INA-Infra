# CU-UP benchmark results

Influx → numpy → plots for CU-UP CPU sweeps on `oai-benchmark`.

**5G / RF / traffic config** (channel, TDD, slice, iperf): see [`docs/oai-benchmark.md`](../../docs/oai-benchmark.md).

```bash
./data_download.py          # all timestamps_*.csv (skips existing data_*/ unless --force)
./data_plot.py              # plots/throughput_vs_cpu_<tag>.png
```
