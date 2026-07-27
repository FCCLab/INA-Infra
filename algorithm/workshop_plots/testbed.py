import os
import re
import pandas as pd
import matplotlib.pyplot as plt

DATA_DIRS = [
    "./CPU_Usage_and_Throughput_with_S5",
    "./CPU_Usage_and_Throughput_with_S5_and_CPU_Control",
]

AXIS_LABEL_FONTSIZE = 24
TICK_FONTSIZE = 24
LEGEND_FONTSIZE = 16


def relabel_s3_s4_s5_to_s5_s6_s7(label: str) -> str:
    """
    修改Label逻辑：
    1. 数字映射: 3->5, 4->6, 5->7
    2. 前缀处理:
       - s3 -> s5, S3 -> S5 (保持原大小写)
       - UE3 -> S5 (UE 强制变为 S)
    """
    mapping = {"3": "5", "4": "6", "5": "7"}

    def repl(m):
        prefix = m.group(1)  # 获取前缀 (s, S, UE, ue)
        number = m.group(2)  # 获取数字 (3, 4, 5)

        new_number = mapping.get(number, number)

        # 如果前缀是 UE 或 ue，强制改为大写 S
        if prefix.upper() == "UE":
            return f"S{new_number}"

        # 否则保持原有前缀 (例如 s3 -> s5, S3 -> S5)
        return f"{prefix}{new_number}"

    # --- 核心修改 ---
    # 1. 使用 (?i) 忽略大小写，这样不用写 [sS]|UE|ue
    # 2. 去掉了末尾的 \b，允许匹配 "S3_CU_UP" 这种情况（即允许数字后跟下划线）
    # 3. (?![\d]) 确保不会匹配到 S30 这种数字
    label = re.sub(r"(?i)\b(s|ue)([345])(?![\d])", repl, label)

    # 处理纯数字的情况
    if re.fullmatch(r"[345]", label.strip()):
        label = mapping[label.strip()]

    return label


def plot_cpu_thr_as_subplots(data_dir: str, out_png: str):
    if not os.path.exists(data_dir):
        print(f"Warning: Directory {data_dir} does not exist. Skipping.")
        return

    cpu_files = sorted([f for f in os.listdir(data_dir) if f.startswith("cpu_") and f.endswith(".csv")])
    thr_files = sorted([f for f in os.listdir(data_dir) if f.startswith("throughput_") and f.endswith(".csv")])

    fig, (ax_cpu, ax_thr) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)

    # CPU
    for csv_file in cpu_files:
        df = pd.read_csv(os.path.join(data_dir, csv_file))
        # 去掉文件名前后缀
        raw_label = csv_file.replace("cpu_", "").replace(".csv", "")
        # 进行重命名
        label = relabel_s3_s4_s5_to_s5_s6_s7(raw_label)

        ax_cpu.plot(df["time_s"], df["cpu_usage_ms_per_s"], label=label, linewidth=2)

    ax_cpu.set_ylabel("CPU Usage (ms/s)", fontsize=AXIS_LABEL_FONTSIZE)
    ax_cpu.tick_params(axis="both", labelsize=TICK_FONTSIZE)
    # 修改：图例放在合适位置，避免遮挡
    ax_cpu.legend(fontsize=LEGEND_FONTSIZE, loc='upper right')

    # Throughput
    for csv_file in thr_files:
        df = pd.read_csv(os.path.join(data_dir, csv_file))
        raw_label = csv_file.replace("throughput_", "").replace(".csv", "")
        label = relabel_s3_s4_s5_to_s5_s6_s7(raw_label)

        ax_thr.plot(df["time_s"], df["throughput_Mbps"], label=label, linewidth=2)

    ax_thr.set_xlabel("Time (s)", fontsize=AXIS_LABEL_FONTSIZE)
    ax_thr.set_ylabel("Throughput (Mbps)", fontsize=AXIS_LABEL_FONTSIZE)
    ax_thr.tick_params(axis="both", labelsize=TICK_FONTSIZE)
    ax_thr.legend(fontsize=LEGEND_FONTSIZE, loc='upper right')

    plt.tight_layout()
    plt.savefig(out_png, dpi=300, bbox_inches="tight")
    plt.show()


# 执行绘图
plot_cpu_thr_as_subplots(
    DATA_DIRS[0],
    "with_S5_cpu_and_throughput_subplots.png"
)

plot_cpu_thr_as_subplots(
    DATA_DIRS[1],
    "with_S5_and_CPU_Control_cpu_and_throughput_subplots.png"
)

# --- 配置区域 ---
DATA_DIRS = [
    "./CPU_Usage_and_Throughput_with_S5",  # 对应 "without"
    "./CPU_Usage_and_Throughput_with_S5_and_CPU_Control",  # 对应 "with"
]

SCENARIO_NAMES = ["without", "with"]

# 定义 s5, s6, s7 的最小吞吐量要求 (Mbps) - 用于违约计算
THRESHOLDS = {
    5: 30,
    6: 45,
    7: 90
}

AXIS_LABEL_FONTSIZE = 24
TICK_FONTSIZE = 24
LEGEND_FONTSIZE = 16


def get_mapped_id(filename: str) -> int:
    """
    从文件名中解析原始数字(3,4,5)，并返回映射后的数字(5,6,7)。
    """
    match = re.search(r"(?i)(?:s|ue)(\d+)", filename)
    if match:
        original_num = int(match.group(1))
        mapping = {3: 5, 4: 6, 5: 7}
        return mapping.get(original_num, original_num)
    return -1


# ==========================================
# 函数 1: 绘制违约量 (Violation) 柱状图
# ==========================================
def plot_system_violation_bar_chart(data_dirs, scenario_names, out_png):
    system_violations = []

    for data_dir in data_dirs:
        if not os.path.exists(data_dir):
            system_violations.append(0)
            continue

        thr_files = sorted([f for f in os.listdir(data_dir) if f.startswith("throughput_") and f.endswith(".csv")])
        scenario_total_violation = 0.0

        for csv_file in thr_files:
            mapped_id = get_mapped_id(csv_file)
            if mapped_id in THRESHOLDS:
                threshold = THRESHOLDS[mapped_id]
                df = pd.read_csv(os.path.join(data_dir, csv_file))
                # 计算违约量
                violation_series = (threshold - df["throughput_Mbps"]).clip(lower=0)
                scenario_total_violation += violation_series.mean()

        system_violations.append(scenario_total_violation)

    # 绘图
    fig, ax = plt.subplots(figsize=(8, 6))
    bars = ax.bar(scenario_names, system_violations, width=0.5, color=['#1f77b4', '#ff7f0e'], alpha=0.8)

    ax.set_ylabel("Violation (Mbps)", fontsize=AXIS_LABEL_FONTSIZE)
    ax.tick_params(axis="x", labelsize=TICK_FONTSIZE)
    ax.tick_params(axis="y", labelsize=TICK_FONTSIZE)

    for bar in bars:
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width() / 2., height,
                f'{height:.2f}', ha='center', va='bottom', fontsize=LEGEND_FONTSIZE)

    plt.tight_layout()
    plt.savefig(out_png, dpi=300, bbox_inches="tight")
    plt.show()


# ==========================================
# 函数 2: 绘制平均吞吐量 (Throughput) 柱状图
# ==========================================
def plot_system_throughput_bar_chart(data_dirs, scenario_names, out_png):
    system_throughputs = []

    for data_dir in data_dirs:
        if not os.path.exists(data_dir):
            system_throughputs.append(0)
            continue

        thr_files = sorted([f for f in os.listdir(data_dir) if f.startswith("throughput_") and f.endswith(".csv")])
        scenario_total_throughput = 0.0

        for csv_file in thr_files:
            # 这里即使不需要阈值，也建议过滤一下文件，或者直接读取所有 throughput_*.csv
            df = pd.read_csv(os.path.join(data_dir, csv_file))

            # 计算该用户的平均吞吐量
            user_avg_throughput = df["throughput_Mbps"].mean()

            # 累加到系统总吞吐量 (System Sum)
            scenario_total_throughput += user_avg_throughput

        system_throughputs.append(scenario_total_throughput)

    # 绘图
    fig, ax = plt.subplots(figsize=(8, 6))
    bars = ax.bar(scenario_names, system_throughputs, width=0.5, color=['#1f77b4', '#ff7f0e'], alpha=0.8)

    # 纵轴标题：Average Throughput
    ax.set_ylabel("Average Throughput (Mbps)", fontsize=AXIS_LABEL_FONTSIZE)
    ax.tick_params(axis="x", labelsize=TICK_FONTSIZE)
    ax.tick_params(axis="y", labelsize=TICK_FONTSIZE)

    for bar in bars:
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width() / 2., height,
                f'{height:.2f}', ha='center', va='bottom', fontsize=LEGEND_FONTSIZE)

    plt.tight_layout()
    plt.savefig(out_png, dpi=300, bbox_inches="tight")
    plt.show()


# --- 执行绘图 ---

print("Plotting Violation Chart...")
plot_system_violation_bar_chart(
    DATA_DIRS,
    SCENARIO_NAMES,
    "system_violation_bar.png"
)

print("Plotting Throughput Chart...")
plot_system_throughput_bar_chart(
    DATA_DIRS,
    SCENARIO_NAMES,
    "system_throughput_bar.png"
)