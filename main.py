import asyncio
import random
import json
import time
from pathlib import Path
from typing import Dict, Any, Optional, List, Set
import aiofiles
from astrbot.api import logger, AstrBotConfig
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.core.star import StarTools

# (v1.5) 导入配置管理器和新的视图渲染器
from utils.config_manager import initialize_data_files
from utils.image_renderer import render_market_image, render_stock_detail_image

# --- 常量定义 (v1.5) ---
PLUGIN_NAME = "astrbot_plugin_stockgame"
DATA_DIR = StarTools.get_data_dir(PLUGIN_NAME)
USER_DATA_DIR = DATA_DIR / "user_data"
STOCKS_FILE = DATA_DIR / "stocks.json"
GLOBAL_EVENTS_FILE = DATA_DIR / "events_global.json"
LOCAL_EVENTS_FILE = DATA_DIR / "events_local.json"
GAME_STATE_FILE = DATA_DIR / "game_state.json"
PLAYING_GROUPS_FILE = DATA_DIR / "playing_groups.json"

CHART_HISTORY_LENGTH = 100

# --- (v1.5) K线图模板已移至 image_renderer.py ---

# --- 数据结构类型提示 (v1.4) ---
StockData = Dict[str, Any]
StockPrices = Dict[str, float]
Portfolio = Dict[str, Any]
GameEvent = Dict[str, Any]
ActiveGameEvent = Dict[str, Any]
PriceHistory = Dict[str, List[float]]



class StockMarketPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.game_lock = asyncio.Lock()
        self.running_task: Optional[asyncio.Task] = None

        # 游戏核心数据 (只读)
        self.stocks_data: Dict[str, StockData] = {}
        self.global_events: List[GameEvent] = []
        self.local_events: List[GameEvent] = []

        # 游戏状态数据 (读写)
        self.stock_prices: StockPrices = {}
        self.active_global_events: List[ActiveGameEvent] = []
        self.playing_groups: Set[str] = set()
        self.price_history: PriceHistory = {}

        asyncio.create_task(self.initialize_plugin())

    async def initialize_plugin(self):
        """
        (v1.4 重构) 异步初始化插件。
        """
        logger.info(f"初始化 {PLUGIN_NAME} (v1.5 视图分离版)...")
        try:
            DATA_DIR.mkdir(exist_ok=True)
            USER_DATA_DIR.mkdir(exist_ok=True)

            await initialize_data_files(DATA_DIR)

            self.stocks_data = await self.load_json_data(STOCKS_FILE)
            if not self.stocks_data:
                logger.error(f"{STOCKS_FILE.name} 为空！插件无法在没有股票的情况下运行。")
                return

            self.global_events = await self.load_json_data(GLOBAL_EVENTS_FILE)
            self.local_events = await self.load_json_data(LOCAL_EVENTS_FILE)

            game_state = await self.load_json_data(GAME_STATE_FILE, default={})
            self.stock_prices = game_state.get("prices", {})
            self.active_global_events = game_state.get("active_global_events", [])
            self.price_history = game_state.get("price_history", {})

            self.playing_groups = set(await self.load_json_data(PLAYING_GROUPS_FILE, default=[]))

            if not self.stock_prices and self.stocks_data:
                logger.info("首次启动，初始化股票价格和K线图历史...")
                for code, data in self.stocks_data.items():
                    initial_price = data.get("initial_price", 100.0)
                    self.stock_prices[code] = initial_price
                    self.price_history[code] = [initial_price]
                await self.save_game_state()

            if self.running_task:
                self.running_task.cancel()

            self.running_task = asyncio.create_task(self.market_ticker())

            logger.info(f"{PLUGIN_NAME} 加载完成。{len(self.active_global_events)} 个全球事件已激活。")

        except Exception as e:
            logger.error(f"{PLUGIN_NAME} 初始化失败: {e}", exc_info=True)

    async def terminate(self):
        """
        插件卸载/停用时调用。
        """
        if self.running_task:
            self.running_task.cancel()
            logger.info("模拟炒股游戏循环已停止。")
        await self.save_game_state()
        logger.info(f"{PLUGIN_NAME} 已卸载。")

    # --- 核心游戏循环 (v1.4 重构) ---

    async def market_ticker(self):
        """
        游戏的主循环，定时更新股市。(v1.4: 增加K线图历史记录)
        """
        tick_interval = self.config.get("tick_interval", 300)
        await asyncio.sleep(5)

        while True:
            try:
                await asyncio.sleep(tick_interval)
                logger.info("股市刷新 (Market Tick)...")

                triggered_local_event: Optional[GameEvent] = None
                triggered_new_global_events: List[GameEvent] = []
                expired_global_events: List[ActiveGameEvent] = []

                # 1. 更新并过滤已激活的全球事件
                next_active_global_events = []
                for event in self.active_global_events:
                    event["remaining_ticks"] -= 1
                    if event["remaining_ticks"] > 0:
                        next_active_global_events.append(event)
                    else:
                        expired_global_events.append(event)
                self.active_global_events = next_active_global_events

                # 2. 判定是否触发 *新* 全球事件
                if self.global_events and random.random() < self.config.get("global_event_chance", 0.1):
                    new_event_template = random.choice(self.global_events)
                    new_active_event: ActiveGameEvent = {
                        **new_event_template,
                        "remaining_ticks": new_event_template.get("duration_ticks", 1),
                        "uid": f"evt_{int(time.time())}"
                    }
                    self.active_global_events.append(new_active_event)
                    triggered_new_global_events.append(new_active_event)

                # 3. 判定是否触发 *突发* 局部事件
                if self.local_events and random.random() < self.config.get("local_event_chance", 0.15):
                    triggered_local_event = random.choice(self.local_events)

                # 4. 价格计算
                async with self.game_lock:
                    new_prices = {}
                    for code, stock in self.stocks_data.items():
                        current_price = self.stock_prices.get(code, stock.get("initial_price", 100.0))

                        new_price = self.calculate_new_price(
                            code, stock, current_price,
                            self.active_global_events,
                            triggered_local_event
                        )
                        new_prices[code] = new_price

                        # --- (v1.4 新增) 记录K线图历史 ---
                        history_list = self.price_history.setdefault(code, [])
                        history_list.append(new_price)
                        if len(history_list) > CHART_HISTORY_LENGTH:
                            self.price_history[code] = history_list[-CHART_HISTORY_LENGTH:]
                        # --- K线图历史记录结束 ---

                    self.stock_prices = new_prices
                    await self.save_game_state()

                    # 5. 构建并推送新闻
                if self.config.get("enable_news_push", True):
                    news_items = []
                    for event in expired_global_events:
                        news_items.append(f"【过期】📉 {event['content']}")
                    for event in triggered_new_global_events:
                        news_items.append(f"【全球】📈 {event['content']} (持续 {event['duration_ticks']} 轮)")
                    if triggered_local_event:
                        news_items.append(f"【突发】🔥 {triggered_local_event['content']}")

                    if news_items:
                        full_news = "📰 【股市快讯】 📰\n" + "\n".join(news_items)
                        await self.push_news_to_groups(full_news)

            except asyncio.CancelledError:
                logger.info("Market ticker被终止。")
                break
            except Exception as e:
                logger.error(f"Market ticker 发生错误: {e}", exc_info=True)
                await asyncio.sleep(60)

    def calculate_new_price(
            self,
            stock_code: str,
            stock_data: StockData,
            current_price: float,
            g_events_list: List[ActiveGameEvent],
            l_event: Optional[GameEvent]
    ) -> float:
        """
        计算单只股票的新价格 (无变化)
        """
        base_volatility = self.config.get("base_volatility", 0.03)
        base_drift = random.uniform(-base_volatility, base_volatility)

        total_trend_impact = 0.0
        for event in g_events_list:
            affected_industries = event.get("affected_industries", [])
            affected_tags = event.get("affected_tags", [])
            if (stock_data.get("industry") in affected_industries or
                    any(tag in stock_data.get("tags", []) for tag in affected_tags)):
                total_trend_impact += event.get("trend_impact", 0.0)

        direct_impact = 0.0
        if l_event:
            affected_codes = l_event.get("affected_codes", [])
            affected_tags = l_event.get("affected_tags", [])
            if (stock_code in affected_codes or
                    any(tag in stock_data.get("tags", []) for tag in affected_tags)):
                direct_impact = l_event.get("direct_impact_percent", 0.0)

        total_drift = base_drift + total_trend_impact
        new_price = current_price * (1 + total_drift)
        new_price = new_price * (1 + direct_impact)

        return max(0.01, new_price)

    async def push_news_to_groups(self, news: str):
        """
        向所有已加入游戏的群组推送新闻。(v1.4.1 修复)
        """
        logger.info(f"推送新闻到 {len(self.playing_groups)} 个群组...")
        for group_id in self.playing_groups:
            try:
                umo = f"aiocqhttp:group:{group_id}"
                # (v1.4.1 修复) 使用 MessageChain().message() 来创建主动消息
                await self.context.send_message(umo, MessageChain().message(news))
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.warning(f"推送新闻到群 {group_id} 失败: {e}")

    # --- 数据持久化 辅助函数 (v1.4 重构) ---

    async def load_json_data(self, file_path: Path, default: Any = None) -> Any:
        try:
            if not file_path.exists():
                return default
            async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                content = await f.read()
                return json.loads(content)
        except Exception as e:
            logger.error(f"加载 {file_path} 失败: {e}", exc_info=True)
            return default if default is not None else {}

    async def save_json_data(self, file_path: Path, data: Any):
        try:
            async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(data, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.error(f"保存 {file_path} 失败: {e}", exc_info=True)

    async def get_user_portfolio(self, event: AstrMessageEvent) -> Optional[Portfolio]:
        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        if not group_id:
            return None
        file_path = USER_DATA_DIR / f"{group_id}_{user_id}.json"
        return await self.load_json_data(file_path, default=None)

    async def create_user_portfolio(self, event: AstrMessageEvent) -> Optional[Portfolio]:
        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        if not group_id:
            return None
        file_path = USER_DATA_DIR / f"{group_id}_{user_id}.json"
        if file_path.exists():
            return await self.get_user_portfolio(event)
        starting_cash = self.config.get("starting_cash", 10000)
        new_portfolio: Portfolio = {"cash": starting_cash, "stocks": {}}
        await self.save_json_data(file_path, new_portfolio)
        return new_portfolio

    async def save_user_portfolio(self, event: AstrMessageEvent, portfolio: Portfolio):
        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        if not group_id:
            return
        file_path = USER_DATA_DIR / f"{group_id}_{user_id}.json"
        await self.save_json_data(file_path, portfolio)

    async def register_group(self, group_id: str):
        if group_id and group_id not in self.playing_groups:
            self.playing_groups.add(group_id)
            await self.save_json_data(PLAYING_GROUPS_FILE, list(self.playing_groups))
            logger.info(f"群组 {group_id} 已加入游戏，将接收新闻推送。")

    async def save_game_state(self):
        """
        (v1.4) 保存游戏状态 (价格、活跃事件、K线图历史)。
        """
        state = {
            "prices": self.stock_prices,
            "active_global_events": self.active_global_events,
            "price_history": self.price_history
        }
        await self.save_json_data(GAME_STATE_FILE, state)

    # --- 指令处理 (v1.5 视图分离) ---

    @filter.command_group("炒股")
    def stock_group(self):
        """ 模拟炒股游戏指令组 """
        # 此处为空，AstrBot会自动处理子命令树

    @stock_group.command("菜单")
    async def show_menu(self, event: AstrMessageEvent):
        """
        (v1.5) 显示游戏帮助菜单 (纯文本)。
        """
        menu = f"""--- 📈 模拟炒股 游戏菜单 📉 ---

/炒股 开户
  - 加入游戏，获取启动资金。

/炒股 市场新闻
  - 查看当前影响市场的“全球事件”(市场气候)。

/炒股 大盘
  - (HTML图片) 查看所有股票的当前价格和市场气候摘要。

/炒股 详情 [股票代码]
  - (K线图) 查看单支股票的详细信息和历史价格曲线。
  - 示例: /炒股 详情 QLAI

/炒股 我的资产
  - 查看你持有的现金和股票。

/炒股 买入 [股票代码] [数量]
  - 购买指定数量的股票。
  - 示例: /炒股 买入 QLAI 10

/炒股 卖出 [股票代码] [数量]
  - 卖出你持有的股票。
  - 示例: /炒股 卖出 QLAI 10
"""
        # (v1.5) 按要求取消T2I，改为纯文本
        yield event.plain_result(menu)

    @stock_group.command("开户")
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def join_game(self, event: AstrMessageEvent):
        """ 加入模拟炒股游戏。 (无变化) """
        user_name = event.get_sender_name()
        portfolio = await self.get_user_portfolio(event)

        if portfolio:
            yield event.plain_result(f"@{user_name} 您已经开户了。使用 /炒股 菜单 查看所有指令。")
            return

        new_portfolio = await self.create_user_portfolio(event)
        if new_portfolio:
            await self.register_group(event.get_group_id())
            yield event.plain_result(
                f"@{user_name} 恭喜您开户成功！\n"
                f"获得启动资金: ${new_portfolio['cash']:.2f}\n"
                f"使用 /炒股 菜单 查看所有指令。"
            )
        else:
            yield event.plain_result("开户失败，似乎无法在私聊中进行游戏。")

    @stock_group.command("市场新闻")
    async def get_news(self, event: AstrMessageEvent):
        """
        (v1.5) 查看当前 *所有* 活跃的市场气候 (纯文本)。
        """
        report = "--- 📰 市场气候报告 📰 ---\n\n"
        if not self.active_global_events:
            report += "目前市场风平浪静，暂无全球性事件影响。\n"
        else:
            report += "以下全球事件正在影响市场：\n\n"
            for e in self.active_global_events:
                impact_str = "利好" if e.get("trend_impact", 0) > 0 else "利空"
                report += f"【{impact_str}】{e['content']}\n"
                report += f"  (剩余时间: {e['remaining_ticks']} 轮刷新)\n\n"

        report += "------------------------\n"
        report += "提示：局部突发事件不会在此显示，会即时推送。"

        # (v1.5) 按要求取消T2I，改为纯文本
        yield event.plain_result(report)

    @stock_group.command("大盘")
    async def view_market(self, event: AstrMessageEvent):
        """
        (v1.5 重构) 使用 HTML 渲染器查看大盘。
        """
        if not self.stock_prices:
            yield event.plain_result("股市尚未开盘，请联系管理员检查插件。")
            return

        stocks_to_render = []
        async with self.game_lock:
            for code, price in self.stock_prices.items():
                stock_info = self.stocks_data.get(code, {})
                history = self.price_history.get(code, [])

                change_str = "N/A"
                color_class = "color-gray"  # 默认灰色

                if len(history) >= 2:
                    prev_price = history[-2]
                    change = price - prev_price
                    change_percent = (change / prev_price) * 100 if prev_price != 0 else 0

                    if change > 0:
                        change_str = f"↑ {change_percent:+.2f}%"
                        color_class = "color-green"  # 涨
                    elif change < 0:
                        change_str = f"↓ {change_percent:+.2f}%"
                        color_class = "color-red"  # 跌
                    else:
                        change_str = "— 0.00%"

                stocks_to_render.append({
                    "code": code,
                    "name": stock_info.get('name', '???'),
                    "price": price,
                    "change_str": change_str,
                    "color_class": color_class
                })

        try:
            # (v1.5) 调用新的渲染器
            img_url = await render_market_image(self, self.active_global_events, stocks_to_render)
            yield event.image_result(img_url)
        except Exception as e:
            logger.error(f"渲染大盘图片失败: {e}，回退到纯文本。")
            yield event.plain_result("渲染大盘图片失败，请检查后台日志。")

    @stock_group.command("详情")
    async def view_stock_detail(self, event: AstrMessageEvent, code: str):
        """
        (v1.5 重构) 使用 HTML 渲染器查看K线图。
        """
        code = code.upper()

        async with self.game_lock:
            stock_info = self.stocks_data.get(code)
            if not stock_info:
                yield event.plain_result(f"错误：未找到股票代码 {code}。")
                return

            current_price = self.stock_prices.get(code, 0.0)
            price_history = self.price_history.get(code, [])

            price_color = "color-gray"
            if len(price_history) >= 2:
                if price_history[-1] > price_history[-2]:
                    price_color = "#28a745"  # 涨 (绿色)
                elif price_history[-1] < price_history[-2]:
                    price_color = "#dc3545"  # 跌 (红色)

            render_data = {
                "stock_name": stock_info.get("name", "未知"),
                "stock_code": code,
                "current_price": f"{current_price:.2f}",
                "stock_industry": stock_info.get("industry", "未知"),
                "stock_tags": stock_info.get("tags", []),
                "price_data_json": json.dumps(price_history),
                "price_color": price_color
            }

        try:
            # (v1.5) 调用新的渲染器
            img_url = await render_stock_detail_image(self, render_data)
            yield event.image_result(img_url)
        except Exception as e:
            logger.error(f"渲染K线图 {code} 失败: {e}", exc_info=True)
            yield event.plain_result(f"渲染股票 {code} 的K线图时发生内部错误。")

    @stock_group.command("我的资产")
    async def view_portfolio(self, event: AstrMessageEvent):
        """
        (v1.5) 查看自己的资产和持仓 (纯文本)。
        """
        user_name = event.get_sender_name()
        portfolio = await self.get_user_portfolio(event)

        if not portfolio:
            yield event.plain_result(f"@{user_name} 您尚未开户，请使用 /炒股 开户 加入游戏。")
            return

        async with self.game_lock:
            cash = portfolio.get("cash", 0.0)
            holdings = portfolio.get("stocks", {})

            report = f"--- @{user_name} 的资产报告 ---\n"
            report += f"💰 可用现金: ${cash:.2f}\n\n"
            report += "📊 持仓详情:\n"

            total_stock_value = 0.0
            if not holdings:
                report += "  (暂无持仓)\n"
            else:
                for code, amount in holdings.items():
                    current_price = self.stock_prices.get(code, 0.0)
                    value = current_price * amount
                    total_stock_value += value
                    stock_name = self.stocks_data.get(code, {}).get("name", "???")
                    report += f"  - 【{code}】{stock_name}\n"
                    report += f"    持有: {amount} 股\n"
                    report += f"    市值: ${value:.2f} (@ ${current_price:.2f}/股)\n"

            total_assets = cash + total_stock_value
            report += "\n------------------------\n"
            report += f"💳 总资产 (现金+市值): ${total_assets:.2f}"

            # (v1.5) 按要求取消T2I，改为纯文本
            yield event.plain_result(report)

    @stock_group.command("买入")
    async def buy_stock(self, event: AstrMessageEvent, code: str, amount_str: str):
        """ 购买股票。 (无变化) """
        user_name = event.get_sender_name()
        portfolio = await self.get_user_portfolio(event)

        if not portfolio:
            yield event.plain_result(f"@{user_name} 您尚未开户。")
            return

        try:
            amount = int(amount_str)
            if amount <= 0: raise ValueError("数量必须为正整数")
        except (ValueError, TypeError):
            yield event.plain_result("购买数量无效。例如: /炒股 买入 QLAI 10")
            return

        code = code.upper()

        async with self.game_lock:
            current_price = self.stock_prices.get(code)

            if current_price is None:
                yield event.plain_result(f"股票代码 {code} 不存在。")
                return

            total_cost = current_price * amount
            cash = portfolio.get("cash", 0.0)

            if cash < total_cost:
                yield event.plain_result(f"@{user_name} 资金不足！购买需 ${total_cost:.2f}，您只有 ${cash:.2f}。")
                return

            portfolio["cash"] = cash - total_cost
            current_holdings = portfolio.get("stocks", {})
            current_holdings[code] = current_holdings.get(code, 0) + amount
            portfolio["stocks"] = current_holdings

            await self.save_user_portfolio(event, portfolio)

            yield event.plain_result(
                f"@{user_name} 交易成功！\n"
                f"👍 **买入** {amount} 股 【{code}】\n"
                f"均价: ${current_price:.2f}\n"
                f"花费: ${total_cost:.2f}\n"
                f"剩余现金: ${portfolio['cash']:.2f}"
            )

    @stock_group.command("卖出")
    async def sell_stock(self, event: AstrMessageEvent, code: str, amount_str: str):
        """ 卖出股票。 (无变化) """
        user_name = event.get_sender_name()
        portfolio = await self.get_user_portfolio(event)

        if not portfolio:
            yield event.plain_result(f"@{user_name} 您尚未开户。")
            return

        try:
            amount = int(amount_str)
            if amount <= 0: raise ValueError("数量必须为正整数")
        except (ValueError, TypeError):
            yield event.plain_result("卖出数量无效。例如: /炒股 卖出 QLAI 10")
            return

        code = code.upper()

        async with self.game_lock:
            current_holdings = portfolio.get("stocks", {})
            held_amount = current_holdings.get(code, 0)

            if held_amount < amount:
                yield event.plain_result(f"@{user_name} 持仓不足！您只有 {held_amount} 股 {code}。")
                return

            current_price = self.stock_prices.get(code)
            if current_price is None:
                yield event.plain_result(f"股票代码 {code} 异常，无法交易。")
                return

            total_profit = current_price * amount

            portfolio["cash"] = portfolio.get("cash", 0.0) + total_profit
            current_holdings[code] = held_amount - amount

            if current_holdings[code] == 0:
                del current_holdings[code]

            portfolio["stocks"] = current_holdings

            await self.save_user_portfolio(event, portfolio)

            yield event.plain_result(
                f"@{user_name} 交易成功！\n"
                f"👎 **卖出** {amount} 股 【{code}】\n"
                f"均价: ${current_price:.2f}\n"
                f"获利: ${total_profit:.2f}\n"
                f"剩余现金: ${portfolio['cash']:.2f}"
            )