from typing import List, Dict
from astrbot.api.star import Star
from astrbot.api import logger
from pathlib import Path

# --- (v1.9 本地化) 加载本地 ApexCharts JS ---
APEXCHARTS_JS_CODE = ""
try:
    # JS_FILE_PATH 会自动定位到当前 .py 文件所在的目录
    JS_FILE_PATH = Path(__file__).parent / "apexcharts.min.js"
    with open(JS_FILE_PATH, 'r', encoding='utf-8') as f:
        APEXCHARTS_JS_CODE = f.read()
    logger.info("本地 apexcharts.min.js 加载成功。")
except Exception as e:
    logger.error(f"加载本地 apexcharts.min.js 失败: {e}。K线图将无法渲染！")
# --- 结束 ---

# --- (v1.6.0: 颜色反转) ---
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
        .climate-item .impact-good { color: #dc3545; font-weight: 600; } /* (v1.6) 利好改红色 */
        .climate-item .impact-bad { color: #28a745; font-weight: 600; } /* (v1.6) 利空改绿色 */
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
        .color-red { color: #dc3545; }   /* (v1.6) 红色 (涨) */
        .color-green { color: #28a745; } /* (v1.6) 绿色 (跌) */
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

# --- (迁移) K线图 HTML 模板 ---
KLINE_CHART_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <script>{{ apexcharts_js | safe }}</script>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background-color: #ffffff;
            color: #212529;
            padding: 15px;
            width: 600px; /* 固定宽度 */
            overflow: hidden;
        }
        #chart { width: 100%; max-width: 600px; }
        .header { margin-bottom: 10px; }
        .stock-name { font-size: 24px; font-weight: 600; }
        .stock-code { font-size: 16px; color: #6c757d; margin-left: 8px; }
        .price { font-size: 28px; font-weight: 700; color: {{ price_color }}; margin-top: 5px; }
        .info { margin-top: 15px; font-size: 14px; }
        .info strong { color: #495057; }
        .tag {
            display: inline-block;
            background-color: #e9ecef;
            color: #495057;
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 12px;
            margin: 2px;
        }
    </style>
</head>
<body>
    <div class="header">
        <span class="stock-name">{{ stock_name }}</span>
        <span class="stock-code">【{{ stock_code }}】</span>
        <div class="price">${{ current_price }}</div>
    </div>
    <div id="chart"></div>
    <div class="info">
        <div><strong>所属行业:</strong> {{ stock_industry }}</div>
        <div>
            <strong>概念标签:</strong>
            {% for tag in stock_tags %}
                <span class="tag">{{ tag }}</span>
            {% endfor %}
        </div>
    </div>
    <script>
        const priceData = {{ price_data_json }};
        const categories = priceData.map((_, index) => `T-${priceData.length - 1 - index}`);
        var options = {
            chart: { type: 'line', height: 250, animations: { enabled: false }, toolbar: { show: false } },
            series: [{ name: '价格', data: priceData }],
            xaxis: {
                categories: categories,
                labels: {
                    show: true,
                    formatter: function (value, timestamp, opts) {
                        const total = categories.length;
                        if (opts.dataPointIndex === 0) return '最早';
                        if (opts.dataPointIndex === total - 1) return '现在';
                        const interval = Math.ceil(total / 10);
                        if (interval > 1 && opts.dataPointIndex % interval === 0) { return value; }
                        return '';
                    }
                },
                tooltip: { enabled: false }
            },
            yaxis: { labels: { formatter: (value) => { return `$${value.toFixed(2)}` } } },
            tooltip: { y: { formatter: (value) => { return `$${value.toFixed(2)}` } } },
            colors: ['{{ price_color }}'], /* (v1.6) 颜色由 main.py 传入 (红或绿) */
            stroke: { curve: 'smooth', width: 3 },
        };
        var chart = new ApexCharts(document.querySelector("#chart"), options);
        chart.render();
    </script>
</body>
</html>
"""


async def render_market_image(star_instance: Star, climate_events: List[Dict], stocks_to_render: List[Dict]) -> str:
    """
    (新增) 使用 html_render 渲染漂亮的大盘图片
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


async def render_stock_detail_image(star_instance: Star, render_data: Dict) -> str:
    """
    (迁移) 使用 html_render 渲染K线图
    """
    try:
        # --- (v1.9 本地化) 注入本地JS代码 ---
        render_data["apexcharts_js"] = APEXCHARTS_JS_CODE
        # --- 结束 ---

        img_url = await star_instance.html_render(
            KLINE_CHART_TEMPLATE,
            render_data,
            options={"timeout": 10000}
        )
        return img_url
    except Exception as e:
        logger.error(f"渲染K线图HTML {render_data.get('stock_code')} 失败: {e}", exc_info=True)
        raise  # 抛出异常，让主逻辑去处理