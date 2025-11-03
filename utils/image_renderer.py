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
RESOURCES_DIR = Path(__file__).parent.parent / "resources"

# 确保tmp目录存在
try:
    TEMP_DIR.mkdir(exist_ok=True, parents=True)
except Exception as e:
    logger.error(f"创建 {TEMP_DIR} 目录失败: {e}")

# 设置 matplotlib 使用 'Agg' 后端
matplotlib.use('Agg')

# (重大修改) 强制加载和注册 `resources` 目录下的所有中文字体
try:
    # 解决负号显示问题
    plt.rcParams['axes.unicode_minus'] = False

    # 1. 确保字体文件夹存在
    if not RESOURCES_DIR.exists():
        RESOURCES_DIR.mkdir(parents=True)
        logger.warning(f"插件 'resources' 目录未找到，已自动创建: {RESOURCES_DIR}")

    # 2. 准备一个列表，存放所有我们成功注册的字体名称
    # 我们将优先使用插件自带的字体
    font_names_to_register = []

    # 3. (关键) 遍历 resources 目录下的所有 .ttf 和 .otf 字体文件
    font_files = list(RESOURCES_DIR.glob("*.ttf")) + list(RESOURCES_DIR.glob("*.otf")) + list(RESOURCES_DIR.glob("*.ttc"))

    if not font_files:
        logger.warning(f"未在 {RESOURCES_DIR} 中找到任何字体文件。将依赖系统字体。")

    for font_path in font_files:
        try:
            font_path_str = str(font_path)
            # 3.1. (关键) 强制将字体文件添加到 Matplotlib 的管理器中
            # 这会更新缓存，让 matplotlib "知道" 这个字体
            fm.fontManager.addfont(font_path_str)

            # 3.2. 获取该字体的内部名称
            prop = fm.FontProperties(fname=font_path_str)
            font_name = prop.get_name()  # e.g., "Source Han Sans CN"

            if font_name not in font_names_to_register:
                font_names_to_register.append(font_name)
            logger.info(f"Matplotlib 成功注册插件字体: {font_name} (来自 {font_path.name})")

        except Exception as e:
            logger.error(f"加载或注册插件字体 {font_path.name} 失败: {e}。")

    # 4. (关键) 添加在Docker/Linux/Windows中常见的备用字体
    # Matplotlib 会按顺序尝试列表中的每个字体
    system_fallbacks = [
        # (我们把在Docker中常见的字体名称也加到备用列表里)
        'WenQuanYi Zen Hei',
        'Noto Sans CJK SC',
        'SimHei',
        'Microsoft YaHei',
        'sans-serif'  # 最后的备用
    ]

    for font in system_fallbacks:
        if font not in font_names_to_register:
            # 检查系统字体是否真的存在，防止无效名称污染列表
            try:
                if fm.findfont(font, fallback_to_default=False):
                    font_names_to_register.append(font)
            except Exception:
                pass  # 字体不存在

    # 确保 'sans-serif' 始终在最后
    if 'sans-serif' not in font_names_to_register:
        font_names_to_register.append('sans-serif')

    # 5. (关键) 设置全局rcParams使用这个“字体列表”
    if not font_names_to_register:
        logger.error("未找到任何可用的中文字体！图表将显示为方块。")
        raise RuntimeError("无可用字体")

    logger.info(f"Matplotlib 全局字体回退列表已设置为: {font_names_to_register}")

    plt.rcParams['font.family'] = 'sans-serif'
    plt.rcParams['font.sans-serif'] = font_names_to_register

except Exception as e:
    logger.error(f"设置 Matplotlib 中文字体时发生严重错误: {e}", exc_info=True)
    # 最终回退
    plt.rcParams['axes.unicode_minus'] = False
    plt.rcParams['font.family'] = 'sans-serif'
    plt.rcParams['font.sans-serif'] = ['sans-serif']

# 大盘视图的HTML模板
MARKET_HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        html, body {
            margin: 0;
            padding: 0;
            background-color: #ffffff;
            color: #212529;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
        }
    
        #root {
            width: 640px;      /* <- 可调整为 600 / 640 等：决定 CSS 宽度 */
            box-sizing: border-box;
            margin: 0 auto;
            padding: 12px;
            overflow: hidden;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
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
        # 在 render_market_image 中使用如下 options：
        options = {
            "timeout": 10000,
            # 请求服务器使用非 full-page 截图（避免捕获多余 viewport 区域）
            "full_page": False,
            # 作为元信息说明我们期望的 CSS 宽度 / DPR —— 由服务器解析并在创建 context 或 clip 时合理使用
            "meta": {"content_css_width": 640, "desired_dpr": 2}
        }

        img_url = await star_instance.html_render(
            MARKET_HTML_TEMPLATE,
            render_data,
            options=options
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
