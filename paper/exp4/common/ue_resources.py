"""Exp4 UE CPU/RAM on usrp (16 CPU / ~64Gi).

Requests stay small so five UEs + OAI RAN still schedule.
Limits are the burst cap so iperf/CCTV/OTT/IoT are not CPU-throttled
or OOM-killed (the old 1 CPU / 1Gi cap stalled those clients).
"""


def ue_ran_resources_yaml() -> str:
    return """        resources:
          requests:
            cpu: 250m
            memory: 256Mi
          limits:
            cpu: "4"
            memory: 4Gi
"""


def app_client_resources_yaml(sid: int = 0) -> str:
    req_cpu = "200m" if sid == 2 else "100m"
    req_mem = "512Mi" if sid == 2 else "256Mi"
    return f"""        resources:
          requests:
            cpu: {req_cpu}
            memory: {req_mem}
          limits:
            cpu: "4"
            memory: 8Gi
"""
