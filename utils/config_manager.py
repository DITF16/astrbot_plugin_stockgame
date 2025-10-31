import aiofiles
import json
from pathlib import Path
from typing import Any
from astrbot.api import logger

# --- 默认股票数据 (中文标签) ---
DEFAULT_STOCKS = {
    "GPHM": {"name": "创世纪药业", "industry": "Med-Bio", "tags": ["新药研发", "专利", "慢性病"], "initial_price": 100},
    "VTAL": {"name": "维生系统", "industry": "Med-Bio", "tags": ["医疗器械", "AI诊断", "智能假肢"],
             "initial_price": 80},
    "CHRO": {"name": "时光生命科学", "industry": "Med-Bio", "tags": ["抗衰老", "基因编辑", "高端医美"],
             "initial_price": 130},
    "APHL": {"name": "顶峰医疗", "industry": "Med-Bio", "tags": ["连锁医院", "医疗保险", "公共卫生"],
             "initial_price": 75},
    "QLAI": {"name": "量子跃迁智能", "industry": "Tech-AI", "tags": ["强人工智能", "算法", "云计算"],
             "initial_price": 150},
    "CYBD": {"name": "赛博动力", "industry": "Tech-AI", "tags": ["机器人", "自动化", "硬件"], "initial_price": 90},
    "HIVE": {"name": "蜂巢互联", "industry": "Tech-AI", "tags": ["社交媒体", "元宇宙", "VR/AR"], "initial_price": 120},
    "NCORE": {"name": "神经核心", "industry": "Tech-AI", "tags": ["脑机接口", "生物芯片", "数据安全"],
              "initial_price": 110},
    "AEGA": {"name": "神盾航空", "industry": "Defense", "tags": ["战斗机", "无人机", "太空探索"], "initial_price": 110},
    "TIDS": {"name": "泰坦防务", "industry": "Defense", "tags": ["地面装甲", "动能武器", "外骨骼"],
             "initial_price": 70},
    "SNTL": {"name": "哨兵网络安全", "industry": "Defense", "tags": ["网络战", "情报", "防火墙"], "initial_price": 85},
    "DSIN": {"name": "深海工业", "industry": "Defense", "tags": ["潜艇", "水下资源", "声纳"], "initial_price": 65},
    "HENG": {"name": "赫利俄斯能源", "industry": "Energy-Mat", "tags": ["清洁能源", "太阳能", "风能"],
             "initial_price": 90},
    "STFN": {"name": "星核聚变", "industry": "Energy-Mat", "tags": ["可控核聚变", "未来能源"], "initial_price": 160},
    "ATLS": {"name": "阿特拉斯矿业", "industry": "Energy-Mat", "tags": ["稀土", "锂矿", "原材料"], "initial_price": 85},
    "CCHM": {"name": "巨像化工", "industry": "Energy-Mat", "tags": ["新型材料", "石化", "工业"], "initial_price": 50},
    "EFFD": {"name": "生态未来食品", "industry": "Consumer", "tags": ["人造肉", "垂直农业", "饮料"],
             "initial_price": 95},
    "CELA": {"name": "星穹服饰", "industry": "Consumer", "tags": ["奢侈品", "智能穿戴", "时尚"], "initial_price": 115},
    "DRMS": {"name": "织梦工作室", "industry": "Consumer", "tags": ["游戏", "电影", "IP运营"], "initial_price": 105},
    "FLOG": {"name": "闪电物流", "industry": "Consumer", "tags": ["电商", "物流", "仓储"], "initial_price": 75}
}

# --- 默认全球事件 (中文标签) ---
DEFAULT_GLOBAL_EVENTS = [
    {
        "content": "“奇点”还是“马戏团”？全球AI开发者大会宣布在“通用基础模型”上取得重大突破！",
        "affected_industries": ["Tech-AI"],
        "affected_tags": ["强人工智能", "云计算", "AI诊断"],
        "trend_impact": 0.015,
        "duration_ticks": 24
    },
    {
        "content": "恐慌！一种新型超级细菌“X-Strain”在全球多地被发现，对现有抗生素产生广谱耐药性！",
        "affected_industries": ["Med-Bio"],
        "affected_tags": ["公共卫生", "连锁医院"],
        "trend_impact": -0.01,
        "duration_ticks": 36
    },
    {
        "content": "历史性协议！“全球碳中和条约”正式签署，各国承诺在10年内将化石能源占比降低50%。",
        "affected_industries": ["Energy-Mat"],
        "affected_tags": ["清洁能源", "未来能源"],
        "trend_impact": 0.02,
        "duration_ticks": 48
    }
]

# --- 默认局部事件 (中文标签) ---
DEFAULT_LOCAL_EVENTS = [
    {
        "content": "致命乌龙！维生系统 (VTAL) 的“AI诊断”软件被曝存在致命Bug。",
        "affected_codes": ["VTAL"],
        "affected_tags": ["AI诊断"],
        "direct_impact_percent": -0.30
    },
    {
        "content": "“秃头”的救赎！创世纪药业 (GPHM) 宣布其生发神药“奇迹森林”III期临床试验100%成功！",
        "affected_codes": ["GPHM"],
        "affected_tags": ["新药研发"],
        "direct_impact_percent": 0.50
    },
    {
        "content": "太酷了！神盾航空 (AEGA) 的六代机“曙光”在试飞中成功“甩尾”击败了5架模拟无人机。",
        "affected_codes": ["AEGA"],
        "affected_tags": ["战斗机"],
        "direct_impact_percent": 0.25
    }
]


async def _create_file_if_not_exists(file_path: Path, default_data: Any):
    """
    一个内部辅助函数，用于异步检查和创建JSON文件。
    """
    if file_path.exists():
        return

    logger.info(f"配置文件 {file_path.name} 未找到，正在创建默认文件...")
    try:
        async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(default_data, ensure_ascii=False, indent=2))
    except Exception as e:
        logger.error(f"创建默认配置文件 {file_path.name} 失败: {e}", exc_info=True)


async def initialize_data_files(data_dir: Path):
    """
    (v1.4) 插件首次启动时，检查并创建所有必需的数据 JSON 文件。
    """
    config_files_to_check = {
        "stocks.json": DEFAULT_STOCKS,
        "events_global.json": DEFAULT_GLOBAL_EVENTS,
        "events_local.json": DEFAULT_LOCAL_EVENTS,
        # (v1.4) game_state.json 现在包含 price_history
        "game_state.json": {"prices": {}, "active_global_events": [], "price_history": {}},
        "playing_groups.json": []
    }

    for filename, default_data in config_files_to_check.items():
        file_path = data_dir / filename
        await _create_file_if_not_exists(file_path, default_data)