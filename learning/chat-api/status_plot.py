"""图表导出与实验逻辑分离；两组使用相同的对数色标。"""
def plot_attention(records, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import LogNorm
    fig, axes = plt.subplots(1, 2, figsize=(16, 6), constrained_layout=True)
    for ax, (arm, record) in zip(axes, records.items()):
        matrix = record["attention"]["matrix"]
        image = ax.imshow(np.maximum(matrix, 1e-7), aspect="auto", cmap="magma",
                          norm=LogNorm(vmin=1e-7, vmax=1))
        for region, color in (("phone_history", "cyan"), ("status_bar", "lime")):
            indices = record["attention"]["regions"][region]
            if indices:
                # 只标区域起点，避免不连续的三段拨号记录被画成一整段。
                starts = [i for k, i in enumerate(indices) if k == 0 or i != indices[k - 1] + 1]
                for start in starts:
                    ax.axvline(start, color=color, lw=.8, alpha=.8)
        ax.set_title(f"{arm}: {'full history' if arm == 'A' else 'history + status'}")
        ax.set_xlabel("Input key token position (cyan: phone records; green: status)")
        ax.set_ylabel("Generated token being predicted")
        fig.colorbar(image, ax=ax, label="Attention weight (last layer, head mean)")
    fig.savefig(path, dpi=150)
    plt.close(fig)
