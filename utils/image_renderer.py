import asyncio
import time
import numpy as np
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from pathlib import Path
from typing import Dict, List
from astrbot.api import logger
from astrbot.api.star import Star
from astrbot.core.star import StarTools

PLUGIN_NAME = "astrbot_plugin_stockgame"
DATA_DIR = StarTools.get_data_dir(PLUGIN_NAME)
TEMP_DIR = DATA_DIR / "tmp"

# 确保tmp目录存在
try:
    TEMP_DIR.mkdir(exist_ok=True, parents=True)
except Exception as e:
    logger.error(f"创建 {TEMP_DIR} 目录失败: {e}")

# 设置 matplotlib 使用 'Agg' 后端，避免GUI问题
matplotlib.use('Agg')

# 设置中文字体
try:
    CHINESE_FONT = None
    # 常见的字体名称列表
    font_names = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS', 'Heiti TC', 'sans-serif']
    for font_name in font_names:
        try:
            # 尝试查找字体
            prop = fm.FontProperties(fname=fm.findfont(fm.FontProperties(family=font_name)))
            CHINESE_FONT = prop.get_name()
            logger.info(f"Matplotlib 找到可用中文字体: {CHINESE_FONT}")
            break
        except Exception:
            continue

    if CHINESE_FONT:
        plt.rcParams['font.sans-serif'] = [CHINESE_FONT]
    else:
        logger.warning("未找到可用的中文字体(如SimHei, Microsoft YaHei)，图表中的中文可能显示为方块。")
    # 解决负号显示问题
    plt.rcParams['axes.unicode_minus'] = False
except Exception as e:
    logger.error(f"设置 Matplotlib 中文字体时出错: {e}")

# 大盘视图的HTML模板
MARKET_HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background-color: #ffffff;
            color: #212529;
            padding: 15px;
            width: 600px; /* 固定宽度，适合截图 */
            overflow: hidden;
        }
        .header { font-size: 24px; font-weight: 600; margin-bottom: 15px; }

        /* 市场气候 */
        .climate-section { margin-bottom: 20px; }
        .climate-header { font-size: 18px; font-weight: 500; margin-bottom: 8px; }
        .climate-item {
            font-size: 14px;
            padding: 5px 0;
            border-bottom: 1px solid #f0f0f0;
        }
        .climate-item .impact-good { color: #dc3545; font-weight: 600; }
        .climate-item .impact-bad { color: #28a745; font-weight: 600; }
        .climate-item .duration { font-size: 12px; color: #6c757d; }
        .climate-empty { font-size: 14px; color: #6c757d; }

        /* 股票列表 */
        .stock-list {
            display: grid;
            grid-template-columns: 1fr 1fr; /* 完美的两列布局 */
            gap: 10px;
        }
        .stock-card {
            border: 1px solid #e9ecef;
            border-radius: 8px;
            padding: 10px;
        }
        .stock-card .name { font-size: 16px; font-weight: 600; }
        .stock-card .code { font-size: 12px; color: #6c757d; margin-left: 5px; }
        .stock-card .price {
            font-size: 20px;
            font-weight: 700;
            margin-top: 5px;
        }
        .stock-card .change { font-size: 14px; font-weight: 500; }
        .color-red { color: #dc3545; }
        .color-green { color: #28a745; }
        .color-gray { color: #6c757d; }

    </style>
</head>
<body>
    <div class="header">📈 模拟股市大盘</div>

    <div class="climate-section">
        <div class="climate-header">当前全球局势</div>
        {% if climate_events %}
            {% for event in climate_events %}
                <div class="climate-item">
                    <span class="{{ 'impact-good' if event.trend_impact > 0 else 'impact-bad' }}">
                        【{{ '利好' if event.trend_impact > 0 else '利空' }}】
                    </span>
                    {{ event.content }}
                    <span class="duration">(剩余: {{ event.remaining_ticks }} 轮)</span>
                </div>
            {% endfor %}
        {% else %}
            <div class="climate-empty">风平浪静，请关注突发事件。</div>
        {% endif %}
    </div>

    <div class="climate-header">实时行情</div>
    <div class="stock-list">
        {% for stock in stocks %}
            <div class="stock-card">
                <div>
                    <span class="name">{{ stock.name }}</span>
                    <span class="code">【{{ stock.code }}】</span>
                </div>
                <div class="price {{ stock.color_class }}">${{ "%.2f"|format(stock.price) }}</div>
                <div class="change {{ stock.color_class }}">{{ stock.change_str }}</div>
            </div>
        {% endfor %}
    </div>

</body>
</html>
"""


async def render_market_image(star_instance: Star, climate_events: List[Dict], stocks_to_render: List[Dict]) -> str:
    """
    使用 html_render 渲染漂亮的大盘图片
    """
    render_data = {
        "climate_events": climate_events,
        "stocks": stocks_to_render
    }
    try:
        # 我们需要从 Star 实例中调用 html_render
        img_url = await star_instance.html_render(
            MARKET_HTML_TEMPLATE,
            render_data,
            options={"timeout": 10000}
        )
        return img_url
    except Exception as e:
        logger.error(f"渲染大盘HTML失败: {e}", exc_info=True)
        raise  # 抛出异常，让主逻辑去处理


# 辅助函数，用于清理临时文件
async def cleanup_temp_files(temp_dir: Path, keep_latest: int = 5):
    """
    异步清理旧的临时图片，防止塞满硬盘
    """
    try:
        # 查找所有 stock_*.png 文件，按修改时间排序
        files = sorted(
            [f for f in temp_dir.glob("stock_*.png") if f.is_file()],
            key=lambda f: f.stat().st_mtime,
            reverse=True
        )

        # 保留最新的 'keep_latest' 个文件，删除其余
        if len(files) > keep_latest:
            files_to_delete = files[keep_latest:]
            for f in files_to_delete:
                f.unlink()
    except Exception as e:
        logger.warning(f"清理临时图片文件失败: {e}")


async def render_stock_detail_image_matplotlib(star_instance: Star, render_data: Dict) -> str:
    """
    使用 Matplotlib 渲染股票详情图, 保存为文件并返回路径
    """

    # 提取数据
    stock_name = render_data.get("stock_name", "未知")
    stock_code = render_data.get("stock_code", "???")
    current_price_str = render_data.get("current_price", "0.00")
    price_color = render_data.get("price_color", "#000000")
    price_data = render_data.get("price_data", [])
    total_shares = render_data.get("total_shares", 0)
    group_id = render_data.get("group_id", None)
    stock_industry = render_data.get("stock_industry", "未知")
    stock_tags = render_data.get("stock_tags", [])

    # 创建图像 (800x600 像素)
    fig, ax = plt.subplots(figsize=(8, 6), dpi=100)
    fig.patch.set_facecolor('#ffffff')  # 设置画布背景为白色
    ax.set_facecolor('#ffffff')  # 设置绘图区背景为白色

    # 绘制主折线图
    prices = np.array(price_data)
    timeline = np.arange(len(prices))

    ax.plot(timeline, prices, color=price_color, linewidth=2.5, zorder=10)

    # 填充图表下方的区域
    ax.fill_between(timeline, prices, color=price_color, alpha=0.1)

    # 设置标题和主要信息
    title = f"{stock_name} ( {stock_code} )"
    # 将标题和价格放在图表顶部，使用 fig.text 精确控制位置
    fig.text(0.05, 0.95, title, fontsize=20, fontweight='bold', ha='left', va='top')
    fig.text(0.05, 0.90, f"${current_price_str}",
             fontsize=24,
             fontweight='bold',
             color=price_color,
             ha='left',
             va='top')

    # (新功能) 在图表右上方显示持仓量
    if group_id:
        shares_text = f"当前群组总持仓: {total_shares} 股"
        fig.text(0.95, 0.90, shares_text,
                 transform=fig.transFigure,
                 fontsize=12,
                 color='#333333',
                 ha='right',
                 va='top')

    # 格式化Y轴 (价格)
    ax.set_ylabel("价格 ($)")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f'${y:.2f}'))
    ax.yaxis.tick_right()
    ax.yaxis.set_label_position("right")
    ax.yaxis.set_label_coords(1.05, 0.5)

    # 格式化X轴 (时间)
    ax.set_xlabel("时间")
    total_ticks = len(timeline)

    # 简化X轴标签，只显示 "最早" 和 "现在"
    ax.set_xticks([0, total_ticks - 1])
    ax.set_xticklabels(['最早', '现在'])
    ax.set_xlim(0, total_ticks - 1)  # 确保图表填满

    # 移除图表边框
    ax.spines['top'].set_visible(False)
    ax.spines['left'].set_visible(False)
    ax.spines['bottom'].set_color('#dddddd')
    ax.spines['right'].set_color('#dddddd')

    # 添加网格线
    ax.grid(True, which='major', axis='y', linestyle='--', color='#e5e5e5', zorder=0)

    # 添加底部的行业和标签信息
    tags_str = "  ".join([f"#{t}" for t in stock_tags])
    info_text = f"所属行业: {stock_industry}\n概念标签: {tags_str if tags_str else '无'}"

    # 调整图表布局，为底部文本留出空间
    plt.subplots_adjust(bottom=0.2, top=0.80)
    fig.text(0.05, 0.1, info_text,
             transform=fig.transFigure,
             fontsize=11,
             color='#555555',
             ha='left',
             va='top',
             wrap=True)

    # 将图像保存到临时文件
    try:
        # 创建一个唯一的文件名
        temp_file_name = f"stock_{stock_code.replace('.', '_')}_{int(time.time() * 1000)}.png"
        temp_file_path = TEMP_DIR / temp_file_name

        # 使用 bbox_inches='tight' 来裁剪空白边缘
        plt.savefig(temp_file_path, format='png', bbox_inches='tight', facecolor=fig.get_facecolor())

        # 异步清理旧的临时图片
        asyncio.create_task(cleanup_temp_files(TEMP_DIR, keep_latest=5))

        # 返回文件路径
        return str(temp_file_path)

    except Exception as e:
        logger.error(f"保存Matplotlib图像 {stock_code} 到文件失败: {e}", exc_info=True)
        raise
    finally:
        plt.close(fig)  # 确保释放内存