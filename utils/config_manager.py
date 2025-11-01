import aiofiles
import json
from pathlib import Path
from typing import Any
from astrbot.api import logger

# --- 默认股票数据 (v1.6.0: 行业中文化) ---
# (这个股票列表保持不变)
DEFAULT_STOCKS = {
    "GPHM": {"name": "创世纪药业", "industry": "医疗生物", "tags": ["新药研发", "专利", "慢性病"], "initial_price": 100},
    "VTAL": {"name": "维生系统", "industry": "医疗生物", "tags": ["医疗器械", "AI诊断", "智能假肢"], "initial_price": 80},
    "CHRO": {"name": "时光生命科学", "industry": "医疗生物", "tags": ["抗衰老", "基因编辑", "高端医美"], "initial_price": 130},
    "APHL": {"name": "顶峰医疗", "industry": "医疗生物", "tags": ["连锁医院", "医疗保险", "公共卫生"], "initial_price": 75},
    "QLAI": {"name": "量子跃迁智能", "industry": "科技AI", "tags": ["强人工智能", "算法", "云计算"], "initial_price": 150},
    "CYBD": {"name": "赛博动力", "industry": "科技AI", "tags": ["机器人", "自动化", "硬件"], "initial_price": 90},
    "HIVE": {"name": "蜂巢互联", "industry": "科技AI", "tags": ["社交媒体", "元宇宙", "VR/AR"], "initial_price": 120},
    "NCORE": {"name": "神经核心", "industry": "科技AI", "tags": ["脑机接口", "生物芯片", "数据安全"], "initial_price": 110},
    "AEGA": {"name": "神盾航空", "industry": "军事国防", "tags": ["战斗机", "无人机", "太空探索"], "initial_price": 110},
    "TIDS": {"name": "泰坦防务", "industry": "军事国防", "tags": ["地面装甲", "动能武器", "外骨骼"], "initial_price": 70},
    "SNTL": {"name": "哨兵网络安全", "industry": "军事国防", "tags": ["网络战", "情报", "防火墙"], "initial_price": 85},
    "DSIN": {"name": "深海工业", "industry": "军事国防", "tags": ["潜艇", "水下资源", "声纳"], "initial_price": 65},
    "HENG": {"name": "赫利俄斯能源", "industry": "能源材料", "tags": ["清洁能源", "太阳能", "风能"], "initial_price": 90},
    "STFN": {"name": "星核聚变", "industry": "能源材料", "tags": ["可控核聚变", "未来能源"], "initial_price": 160},
    "ATLS": {"name": "阿特拉斯矿业", "industry": "能源材料", "tags": ["稀土", "锂矿", "原材料"], "initial_price": 85},
    "CCHM": {"name": "巨像化工", "industry": "能源材料", "tags": ["新型材料", "石化", "工业"], "initial_price": 50},
    "EFFD": {"name": "生态未来食品", "industry": "消费娱乐", "tags": ["人造肉", "垂直农业", "饮料"], "initial_price": 95},
    "CELA": {"name": "星穹服饰", "industry": "消费娱乐", "tags": ["奢侈品", "智能穿戴", "时尚"], "initial_price": 115},
    "DRMS": {"name": "织梦工作室", "industry": "消费娱乐", "tags": ["游戏", "电影", "IP运营"], "initial_price": 105},
    "FLOG": {"name": "闪电物流", "industry": "消费娱乐", "tags": ["电商", "物流", "仓储"], "initial_price": 75}
}

# --- (v1.8.0) 默认全球事件 (扩充至10个，覆盖5大行业) ---
DEFAULT_GLOBAL_EVENTS = [
    # 医疗生物
    {
        "content": "全球卫生峰会召开，各国承诺在未来十年内大幅增加“医疗生物”领域的公共支出。",
        "affected_industries": ["医疗生物"],
        "affected_tags": ["公共卫生", "医疗器械"],
        "trend_impact": 0.01,
        "duration_ticks": 30
    },
    {
        "content": "重大医疗丑闻曝光，公众对全球医疗体系信任度下降，监管机构开始严查“专利”药。",
        "affected_industries": ["医疗生物"],
        "affected_tags": ["连锁医院", "专利"],
        "trend_impact": -0.01,
        "duration_ticks": 30
    },
    # 科技AI
    {
        "content": "“奇点”还是“马戏团”？全球AI开发者大会宣布在“通用基础模型”上取得重大突破！",
        "affected_industries": ["科技AI"],
        "affected_tags": ["强人工智能", "云计算", "AI诊断"],
        "trend_impact": 0.015,
        "duration_ticks": 24
    },
    {
        "content": "“数据主权法”高墙筑起！各国纷纷出台史上最严数据隐私法，禁止数据跨境流动。",
        "affected_industries": ["科技AI"],
        "affected_tags": ["云计算", "社交媒体", "算法"],
        "trend_impact": -0.015,
        "duration_ticks": 30
    },
    # 军事国防
    {
        "content": "地缘政治局势紧张，全球多国紧急追加“军事国防”采购预算。",
        "affected_industries": ["军事国防"],
        "affected_tags": ["战斗机", "地面装甲", "网络战"],
        "trend_impact": 0.02,
        "duration_ticks": 20
    },
    {
        "content": "“永久和平条约”意外签署！全球主要大国宣布将削减30%的“军事国防”开支。",
        "affected_industries": ["军事国防"],
        "affected_tags": [],
        "trend_impact": -0.025,
        "duration_ticks": 40
    },
    # 能源材料
    {
        "content": "历史性协议！“全球碳中和条约”正式签署，各国承诺对“清洁能源”进行巨额补贴。",
        "affected_industries": ["能源材料"],
        "affected_tags": ["清洁能源", "未来能源", "锂矿"],
        "trend_impact": 0.015,
        "duration_ticks": 48
    },
    {
        "content": "OPEC意外宣布大幅增产，原油价格暴跌，导致“石化”和传统“能源材料”行业恐慌。",
        "affected_industries": ["能源材料"],
        "affected_tags": ["石化", "原材料"],
        "trend_impact": -0.01,
        "duration_ticks": 20
    },
    # 消费娱乐
    {
        "content": "全球“全民基本收入”(UBI)实验启动，分析师预测“消费娱乐”行业将迎来狂欢。",
        "affected_industries": ["消费娱乐"],
        "affected_tags": ["电商", "游戏", "奢侈品"],
        "trend_impact": 0.015,
        "duration_ticks": 24
    },
    {
        "content": "“反成瘾法案”出台！多国联合立法，强制限制“奶头乐”产业，征收高额“精神健康税”。",
        "affected_industries": ["消费娱乐", "科技AI"],
        "affected_tags": ["社交媒体", "元宇宙", "游戏"],
        "trend_impact": -0.015,
        "duration_ticks": 30
    }
]

# --- (v1.8.0) 默认局部事件 (扩充至40个，全面平衡) ---
DEFAULT_LOCAL_EVENTS = [
    # 医疗生物 (8)
    {
        "content": "“秃头”的救赎！一款“新药研发”产品临床试验100%成功！",
        "affected_tags": ["新药研发"], "direct_impact_percent": 0.15
    },
    {
        "content": "重大丑闻！某“新药研发”公司被曝临床数据造假，股价暴跌。",
        "affected_tags": ["新药研发"], "direct_impact_percent": -0.15
    },
    {
        "content": "“AI医生”！某“AI诊断”系统提前预警重大疾病，挽救了患者生命！",
        "affected_tags": ["AI诊断"], "direct_impact_percent": 0.12
    },
    {
        "content": "致命乌龙！某“AI诊断”软件被曝存在致命Bug。",
        "affected_tags": ["AI诊断"], "direct_impact_percent": -0.12
    },
    {
        "content": "“青春逆转”？顶级富豪证实“抗衰老”疗法有效，订单激增。",
        "affected_tags": ["抗衰老"], "direct_impact_percent": 0.18
    },
    {
        "content": "监管机构叫停“抗衰老”疗法，称其存在未知的严重副作用。",
        "affected_tags": ["抗衰老"], "direct_impact_percent": -0.16
    },
    {
        "content": "“连锁医院”因高效运营模式获得政府巨额补贴。",
        "affected_tags": ["连锁医院"], "direct_impact_percent": 0.08
    },
    {
        "content": "天价账单！“连锁医院”被曝“天价纱布”丑闻，面临集体诉讼。",
        "affected_tags": ["连锁医院"], "direct_impact_percent": -0.10
    },
    # 科技AI (8)
    {
        "content": "重大突破！“强人工智能”模型通过图灵测试，引发全球关注。",
        "affected_tags": ["强人工智能"], "direct_impact_percent": 0.15
    },
    {
        "content": "“逻辑炸弹”病毒爆发！全球“强人工智能”模型开始出现大规模“胡言乱语”。",
        "affected_tags": ["强人工智能"], "direct_impact_percent": -0.15
    },
    {
        "content": "“机器人”护工投入使用，有效解决了全球劳动力短缺问题。",
        "affected_tags": ["机器人"], "direct_impact_percent": 0.12
    },
    {
        "content": "“机器人叛乱”预演？某工厂“机器人”突然失控，引发公众担忧。",
        "affected_tags": ["机器人"], "direct_impact_percent": -0.10
    },
    {
        "content": "“元宇宙”退潮？“社交媒体”巨头宣布裁撤其“VR/AR”部门。",
        "affected_tags": ["社交媒体", "元宇宙", "VR/AR"], "direct_impact_percent": -0.12
    },
    {
        "content": "现象级！某“社交媒体”平台月活突破30亿，广告收入创纪录。",
        "affected_tags": ["社交媒体"], "direct_impact_percent": 0.10
    },
    {
        "content": "“读心术”成真！“脑机接口”技术成功帮助“渐冻症”患者恢复交流。",
        "affected_tags": ["脑机接口"], "direct_impact_percent": 0.18
    },
    {
        "content": "黑客入侵“脑机接口”！某公司“生物芯片”被曝存在致命后门。",
        "affected_tags": ["脑机接口", "生物芯片", "数据安全"], "direct_impact_percent": -0.16
    },
    # 军事国防 (8)
    {
        "content": "太酷了！“战斗机”六代机“曙光”在试飞中成功“甩尾”击败了5架模拟无人机。",
        "affected_tags": ["战斗机"], "direct_impact_percent": 0.20
    },
    {
        "content": "订单取消！某国宣布削减“战斗机”采购预算，转向发展“无人机”。",
        "affected_tags": ["战斗机"], "direct_impact_percent": -0.15
    },
    {
        "content": "“外骨骼”装甲帮助瘫痪士兵重新站立，军方下达大额订单。",
        "affected_tags": ["外骨骼"], "direct_impact_percent": 0.12
    },
    {
        "content": "“废铁”还是“泰坦”？“外骨骼”装甲在演示中被冰雹“击穿”了散热系统。",
        "affected_tags": ["外骨骼"], "direct_impact_percent": -0.12
    },
    {
        "content": "完美防御！“量子防火墙”成功抵御了史诗级网络攻击。",
        "affected_tags": ["网络战", "防火墙"], "direct_impact_percent": 0.15
    },
    {
        "content": "“后门”漏洞！某“网络战”公司产品被曝存在安全漏洞，国防机密泄露。",
        "affected_tags": ["网络战", "情报"], "direct_impact_percent": -0.14
    },
    {
        "content": "“深海”静音技术突破！新型“潜艇”无法被声纳侦测。",
        "affected_tags": ["潜艇", "声纳"], "direct_impact_percent": 0.15
    },
    {
        "content": "“潜艇”建造合同因成本严重超支而被军方取消。",
        "affected_tags": ["潜艇"], "direct_impact_percent": -0.13
    },
    # 能源材料 (8)
    {
        "content": "“光伏”技术突破！新型“清洁能源”太阳能板效率翻倍。",
        "affected_tags": ["清洁能源", "太阳能"], "direct_impact_percent": 0.12
    },
    {
        "content": "骗局？某“清洁能源”大型电站被曝数据造假，夜间储能效率几乎为零。",
        "affected_tags": ["清洁能源"], "direct_impact_percent": -0.10
    },
    {
        "content": "能量“净增益”重大突破！“可控核聚变”商业化比预期提前10年！",
        "affected_tags": ["可控核聚变", "未来能源"], "direct_impact_percent": 0.20
    },
    {
        "content": "“可控核聚变”实验反应堆发生泄漏，项目被监管机构紧急叫停。",
        "affected_tags": ["可控核聚变"], "direct_impact_percent": -0.18
    },
    {
        "content": "挖到宝了！在南极冰下发现超巨型“稀土”矿脉！",
        "affected_tags": ["稀土", "原材料"], "direct_impact_percent": 0.15
    },
    {
        "content": "“稀土”的终结？科学家发现“稀土”的廉价替代材料，价格暴跌。",
        "affected_tags": ["稀土"], "direct_impact_percent": -0.15
    },
    {
        "content": "“塑料”终结者！“新型材料”被研发，100%可降解且硬度堪比钢铁。",
        "affected_tags": ["新型材料"], "direct_impact_percent": 0.14
    },
    {
        "content": "“新型材料”被证实有毒，所有已发售产品被全球召回。",
        "affected_tags": ["新型材料"], "direct_impact_percent": -0.16
    },
    # 消费娱乐 (8)
    {
        "content": "口感以假乱真！“人造肉”汉堡在盲测中击败了真正的牛肉。",
        "affected_tags": ["人造肉"], "direct_impact_percent": 0.10
    },
    {
        "content": "口味丑闻！“人造肉”被知名美食家直播评价为“像潮湿的硬纸板”。",
        "affected_tags": ["人造肉"], "direct_impact_percent": -0.12
    },
    {
        "content": "“奢侈品”巨头宣布全球涨价，引发消费者抢购潮。",
        "affected_tags": ["奢侈品"], "direct_impact_percent": 0.08
    },
    {
        "content": "“皇帝的新衣”！“奢侈品”智能夹克被曝成本仅10美元且侵犯隐私。",
        "affected_tags": ["奢侈品", "智能穿戴"], "direct_impact_percent": -0.10
    },
    {
        "content": "史诗级IP！“游戏”工作室宣布其电影改编大获成功，在线人数突破1亿！",
        "affected_tags": ["游戏", "电影", "IP运营"], "direct_impact_percent": 0.15
    },
    {
        "content": "“游戏”大作翻车！Bug 遍地，服务器崩溃，玩家集体退款。",
        "affected_tags": ["游戏"], "direct_impact_percent": -0.15
    },
    {
        "content": "“黑五”再创纪录！“电商”销售额同比增长50%，“物流”订单爆满。",
        "affected_tags": ["电商", "物流"], "direct_impact_percent": 0.10
    },
    {
        "content": "“物流”大罢工！“电商”仓库爆仓，配送全面瘫痪。",
        "affected_tags": ["电商", "物流"], "direct_impact_percent": -0.12
    }
]

# --- (v1.6.0) 自动创建文件的函数 ---

async def _create_file_if_not_exists(file_path: Path, default_data: Any):
    if file_path.exists():
        return
    logger.info(f"配置文件 {file_path.name} 未找到，正在创建默认文件...")
    try:
        async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(default_data, ensure_ascii=False, indent=2))
    except Exception as e:
        logger.error(f"创建默认配置文件 {file_path.name} 失败: {e}", exc_info=True)


async def initialize_data_files(data_dir: Path):
    config_files_to_check = {
        "stocks.json": DEFAULT_STOCKS,
        "events_global.json": DEFAULT_GLOBAL_EVENTS,
        "events_local.json": DEFAULT_LOCAL_EVENTS,
        "game_state.json": {
            "prices": {},
            "active_global_events": [],
            "price_history": {},
            "last_local_event_news": "暂无突发事件。"
        },
        "playing_groups.json": []
    }

    for filename, default_data in config_files_to_check.items():
        file_path = data_dir / filename
        await _create_file_if_not_exists(file_path, default_data)