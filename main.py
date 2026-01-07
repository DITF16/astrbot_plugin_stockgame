import asyncio
import random
import json
import time
import aiosqlite
from pathlib import Path
from typing import Dict, Any, Optional, List, Set
import aiofiles
import astrbot.api.message_components as Comp
from astrbot.api import logger, AstrBotConfig
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star
from astrbot.core.star import StarTools
from .utils.config_manager import initialize_data_files
from .utils.image_renderer import render_market_image, render_stock_detail_image_matplotlib


PLUGIN_NAME = "astrbot_plugin_stockgame"
DATA_DIR = StarTools.get_data_dir(PLUGIN_NAME)
# USER_DATA_DIR = DATA_DIR / "user_data"
DB_FILE = DATA_DIR / "portfolios.sqlite"
STOCKS_FILE = DATA_DIR / "stocks.json"
GLOBAL_EVENTS_FILE = DATA_DIR / "events_global.json"
LOCAL_EVENTS_FILE = DATA_DIR / "events_local.json"
GAME_STATE_FILE = DATA_DIR / "game_state.json"
PLAYING_GROUPS_FILE = DATA_DIR / "playing_groups.json"

CHART_HISTORY_LENGTH = 100

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

        self.db_path = DB_FILE

        # 游戏核心数据 (只读)
        self.stocks_data: Dict[str, StockData] = {}
        self.global_events: List[GameEvent] = []
        self.local_events: List[GameEvent] = []

        # 游戏状态数据 (读写)
        self.stock_prices: StockPrices = {}
        self.active_global_events: List[ActiveGameEvent] = []
        self.playing_groups: Set[str] = set()
        self.price_history: PriceHistory = {}
        self.last_local_event_news: str = "暂无突发事件。"

        asyncio.create_task(self.initialize_plugin())

    async def initialize_plugin(self):
        """
        异步初始化插件。
        """
        logger.info(f"初始化 {PLUGIN_NAME}...")
        try:
            DATA_DIR.mkdir(exist_ok=True)
            await self.init_database()

            await initialize_data_files(DATA_DIR)

            self.stocks_data = await self.load_json_data(STOCKS_FILE)
            if not self.stocks_data:
                logger.error(f"{STOCKS_FILE.name} 为空！插件无法在没有股票的情况下运行。")
                return

            self.global_events = await self.load_json_data(GLOBAL_EVENTS_FILE)
            self.local_events = await self.load_json_data(LOCAL_EVENTS_FILE)

            # 加载游戏状态
            game_state = await self.load_json_data(GAME_STATE_FILE, default={})
            self.stock_prices = game_state.get("prices", {})
            self.active_global_events = game_state.get("active_global_events", [])
            self.price_history = game_state.get("price_history", {})
            # 加载最新突发新闻
            self.last_local_event_news = game_state.get("last_local_event_news", "暂无突发事件。")

            self.playing_groups = set(await self.load_json_data(PLAYING_GROUPS_FILE, default=[]))

            # 初始化价格和K线图历史
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

    async def init_database(self):
        """
        初始化SQLite数据库和表结构
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # 开启 WAL 模式提高并发性
                await db.executescript("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;")

                # 创建玩家资产表
                await db.execute("""
                                 CREATE TABLE IF NOT EXISTS portfolios
                                 (
                                     user_id  TEXT NOT NULL,
                                     group_id TEXT NOT NULL,
                                     cash     REAL DEFAULT 0,
                                     PRIMARY KEY (user_id, group_id)
                                 )
                                 """)

                # 创建玩家持仓表
                await db.execute("""
                                 CREATE TABLE IF NOT EXISTS holdings
                                 (
                                     user_id       TEXT    NOT NULL,
                                     group_id      TEXT    NOT NULL,
                                     stock_code    TEXT    NOT NULL,
                                     amount        INTEGER NOT NULL,
                                     avg_buy_price REAL    NOT NULL,
                                     PRIMARY KEY (user_id, group_id, stock_code),
                                     FOREIGN KEY (user_id, group_id)
                                         REFERENCES portfolios (user_id, group_id)
                                         ON DELETE CASCADE
                                 )
                                 """)
                await db.commit()
            logger.info("数据库 portfolios.sqlite 初始化/连接成功。")
        except Exception as e:
            logger.error(f"数据库 {self.db_path} 初始化失败: {e}", exc_info=True)
            raise e

    async def terminate(self):
        """
        插件卸载/停用时调用。
        """
        if self.running_task:
            self.running_task.cancel()
            logger.info("模拟炒股游戏循环已停止。")
        await self.save_game_state()
        logger.info(f"{PLUGIN_NAME} 已卸载。")


    async def market_ticker(self):
        """
        游戏主循环，增加突发新闻存储
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
                local_news = ""

                # 1. 更新并过滤已激活的全球事件
                next_active_global_events = []
                for event in self.active_global_events:
                    event["remaining_ticks"] -= 1
                    if event["remaining_ticks"] > 0:
                        next_active_global_events.append(event)
                    else:
                        expired_global_events.append(event)
                self.active_global_events = next_active_global_events

                # 2. 判定是否触发 新 全球事件
                if self.global_events and random.random() < self.config.get("global_event_chance", 0.1):
                    new_event_template = random.choice(self.global_events)
                    new_active_event: ActiveGameEvent = {
                        **new_event_template,
                        "remaining_ticks": new_event_template.get("duration_ticks", 1),
                        "uid": f"evt_{int(time.time())}"
                    }
                    self.active_global_events.append(new_active_event)
                    triggered_new_global_events.append(new_active_event)

                # 3. 判定是否触发 突发 局部事件
                if self.local_events and random.random() < self.config.get("local_event_chance", 0.15):
                    triggered_local_event = random.choice(self.local_events)
                    # 存储突发新闻
                    local_news = f"【突发】🔥 {triggered_local_event['content']}"
                    self.last_local_event_news = local_news

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

                        history_list = self.price_history.setdefault(code, [])
                        history_list.append(new_price)
                        if len(history_list) > CHART_HISTORY_LENGTH:
                            self.price_history[code] = history_list[-CHART_HISTORY_LENGTH:]

                    self.stock_prices = new_prices
                    await self.save_game_state()  # 此时会保存K线图和最新突发新闻

                # 5. 构建并推送新闻
                if self.config.get("enable_news_push", True):
                    news_items = []
                    for event in expired_global_events:
                        news_items.append(f"【过期】📉 {event['content']}")
                    for event in triggered_new_global_events:
                        news_items.append(f"【全球】📈 {event['content']} (持续 {event['duration_ticks']} 轮)")
                    if local_news:
                        news_items.append(local_news)

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
        计算单只股票的新价格
        """
        # 基础随机波动
        base_volatility = self.config.get("base_volatility", 0.03)
        base_drift = random.uniform(-base_volatility, base_volatility)

        # 全球事件趋势取平均值，以防止极端复利
        trend_impacts = []
        for event in g_events_list:
            affected_industries = event.get("affected_industries", [])
            affected_tags = event.get("affected_tags", [])
            if (stock_data.get("industry") in affected_industries or
                    any(tag in stock_data.get("tags", []) for tag in affected_tags)):
                trend_impacts.append(event.get("trend_impact", 0.0))

        total_trend_impact = 0.0
        if trend_impacts:
            # 取平均值，而不是总和
            total_trend_impact = sum(trend_impacts) / len(trend_impacts)

        # 局部事件冲击
        direct_impact = 0.0
        if l_event:
            affected_codes = l_event.get("affected_codes", [])
            affected_tags = l_event.get("affected_tags", [])
            if (stock_code in affected_codes or
                    any(tag in stock_data.get("tags", []) for tag in affected_tags)):
                direct_impact = l_event.get("direct_impact_percent", 0.0)

        clamped_direct_impact = max(-0.5, min(direct_impact, 1.0))

        # 价格计算 (引入均值回归)

        # A. 应用趋势和波动 (乘法)
        total_drift = base_drift + total_trend_impact
        price_after_drift = current_price * (1 + total_drift)

        # B. 应用均值回归 (加法)
        reversion_strength = self.config.get("mean_reversion_strength", 0.005)
        # 股票的“基准价”
        fundamental_value = stock_data.get("initial_price", 100.0)

        # 计算 (基准价 - 当前价) * 强度，得出一个“拉力”
        reversion_pull = (fundamental_value - price_after_drift) * reversion_strength

        # 应用拉力
        price_after_reversion = price_after_drift + reversion_pull

        # C. 应用一次性的局部事件冲击 (乘法)
        new_price = price_after_reversion * (1 + clamped_direct_impact)  # 使用钳制后的值

        # 保证价格不低于 0.01
        return max(0.01, new_price)

    async def push_news_to_groups(self, news: str):
        """
        向所有已加入游戏的群组推送新闻。
        若发现机器人已不在群组中，则自动关闭该群的推送。
        """
        umo_prefix = self.config.get("umo_group_prefix", "NapCat:GroupMessage")
        logger.info(f"推送新闻到 {len(self.playing_groups)} 个群组...")
        groups_to_check = list(self.playing_groups)

        for group_id in groups_to_check:
            try:
                # 使用配置的前缀
                umo = f"{umo_prefix}:{group_id}"

                await self.context.send_message(umo, MessageChain().message(news))
                await asyncio.sleep(0.5)
            except Exception as e:
                error_msg = str(e)
                # 检查是否是因为被踢出群组导致的失败 (错误码 110 或相关提示)
                if "110" in error_msg or "被移出该群" in error_msg:
                    logger.warning(f"检测到机器人已不在群组 {group_id}，正在移除该群推送记录。")
                    await self.disable_push_in_group(group_id)
                else:
                    logger.error(f"!!! 推送新闻到群 {group_id} 失败: {e}", exc_info=True)

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

    async def get_user_portfolio(self, user_id: str, group_id: str) -> Optional[Portfolio]:
        if not group_id or not user_id:
            return None

        try:
            async with aiosqlite.connect(self.db_path) as db:
                # 1. 获取现金
                async with db.execute(
                        "SELECT cash FROM portfolios WHERE user_id = ? AND group_id = ?",
                        (user_id, group_id)
                ) as cursor:
                    row = await cursor.fetchone()
                    if not row:
                        return None  # 用户不存在

                    portfolio: Portfolio = {"cash": row[0], "stocks": {}}

                # 2. 获取持仓
                async with db.execute(
                        "SELECT stock_code, amount, avg_buy_price FROM holdings WHERE user_id = ? AND group_id = ?",
                        (user_id, group_id)
                ) as cursor:
                    async for stock_code, amount, avg_buy_price in cursor:
                        portfolio["stocks"][stock_code] = {
                            "amount": amount,
                            "avg_buy_price": avg_buy_price
                        }

                return portfolio

        except Exception as e:
            logger.error(f"从DB获取 {user_id} (群 {group_id}) portfolio失败: {e}", exc_info=True)
            return None

    async def create_user_portfolio(self, user_id: str, group_id: str) -> Optional[Portfolio]:
        if not group_id or not user_id:
            return None

        # 检查是否已存在
        existing_portfolio = await self.get_user_portfolio(user_id, group_id)
        if existing_portfolio:
            return existing_portfolio

        # 不存在则创建
        starting_cash = self.config.get("starting_cash", 10000)
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "INSERT INTO portfolios (user_id, group_id, cash) VALUES (?, ?, ?)",
                    (user_id, group_id, starting_cash)
                )
                await db.commit()

            new_portfolio: Portfolio = {"cash": starting_cash, "stocks": {}}
            return new_portfolio
        except Exception as e:
            logger.error(f"创建用户 {user_id} (群 {group_id}) portfolio失败: {e}", exc_info=True)
            return None

    async def save_user_portfolio(self, user_id: str, group_id: str, portfolio: Portfolio):
        if not group_id or not user_id:
            return

        cash = portfolio.get("cash", 0.0)
        stocks = portfolio.get("stocks", {})

        try:
            # aiosqlite.connect() 的 "async with" 块本身就是一个事务。
            # 它会在块成功结束时自动 commit，在发生异常时自动 rollback。
            async with aiosqlite.connect(self.db_path) as db:

                # 1. 更新现金
                await db.execute(
                    "UPDATE portfolios SET cash = ? WHERE user_id = ? AND group_id = ?",
                    (cash, user_id, group_id)
                )

                # 2. 清空旧持仓
                await db.execute(
                    "DELETE FROM holdings WHERE user_id = ? AND group_id = ?",
                    (user_id, group_id)
                )

                # 3. 插入新持仓
                holdings_data = []
                for code, data in stocks.items():
                    if data.get("amount", 0) > 0:  # 只插入有数量的
                        holdings_data.append((
                            user_id, group_id, code,
                            data.get("amount"),
                            data.get("avg_buy_price")
                        ))

                if holdings_data:
                    await db.executemany(
                        "INSERT INTO holdings (user_id, group_id, stock_code, amount, avg_buy_price) VALUES (?, ?, ?, ?, ?)",
                        holdings_data
                    )

                await db.commit()

        except Exception as e:
            # (自动 rollback 在这里发生)
            logger.error(f"保存DB {user_id} (群 {group_id}) portfolio失败: {e}", exc_info=True)

    # 获取群组总持仓的方法
    async def get_total_shares_in_group(self, group_id: str, stock_code: str) -> int:
        """
        (SQLite版) 异步计算一个群组中特定股票的总持仓量
        """
        if not group_id:
            return 0

        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute(
                        "SELECT SUM(amount) FROM holdings WHERE group_id = ? AND stock_code = ?",
                        (group_id, stock_code)
                ) as cursor:
                    row = await cursor.fetchone()
                    # 如果查询结果为 None (即无人持有)，返回 0
                    return row[0] if row and row[0] is not None else 0
        except Exception as e:
            logger.error(f"从DB计算群 {group_id} {stock_code} 总持仓失败: {e}", exc_info=True)
            return 0


    async def enable_push_in_group(self, group_id: str):
        if group_id and group_id not in self.playing_groups:
            self.playing_groups.add(group_id)
            await self.save_json_data(PLAYING_GROUPS_FILE, list(self.playing_groups))
            logger.info(f"群组 {group_id} 已开启新闻推送。")


    async def disable_push_in_group(self, group_id: str):
        if group_id and group_id in self.playing_groups:
            self.playing_groups.remove(group_id)
            await self.save_json_data(PLAYING_GROUPS_FILE, list(self.playing_groups))
            logger.info(f"群组 {group_id} 已关闭新闻推送。")

    async def save_game_state(self):
        """
        保存游戏状态 (价格、活跃事件、K线图、最新突发)。
        """
        state = {
            "prices": self.stock_prices,
            "active_global_events": self.active_global_events,
            "price_history": self.price_history,
            "last_local_event_news": self.last_local_event_news
        }
        await self.save_json_data(GAME_STATE_FILE, state)


    @filter.command_group("炒股")
    def stock_group(self):
        """ 模拟炒股游戏指令组 """

    @stock_group.command("菜单")
    async def show_menu(self, event: AstrMessageEvent):
        """
        显示游戏帮助菜单。
        """
        menu = f"""--- 📈 模拟炒股 游戏菜单 📉 ---

/炒股 开启推送
  - (群聊) 在本群开启股市新闻(全球/突发)推送。

/炒股 关闭推送
  - (群聊) 在本群关闭股市新闻推送。

/炒股 开户
  - 加入游戏，获取启动资金。

/炒股 全球局势
  - 查看当前影响市场的“全球事件”。

/炒股 新闻
  - 查看最近一次发生的“突发事件”。

/炒股 大盘
  - 查看所有股票的当前价格和全球局势。

/炒股 详情 [股票代码]
  - (K线图) 查看单支股票的详细信息和历史价格曲线。
  - 示例: /炒股 详情 QLAI

/炒股 我的资产
  - 查看你持有的现金和股票。

/炒股 买入 [股票代码] [数量]
  - 示例: /炒股 买入 QLAI 10

/炒股 卖出 [股票代码] [数量]
  - 示例: /炒股 卖出 QLAI 10

/炒股 全部卖出
  - 卖出你的所有类型股票
"""
        yield event.plain_result(menu)

    @stock_group.command("开启推送")
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def enable_push(self, event: AstrMessageEvent):
        """
        在本群开启新闻推送。
        """
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("此功能仅限群聊使用。")
            return

        if group_id in self.playing_groups:
            yield event.plain_result("本群的新闻推送已经处于开启状态。")
            return

        await self.enable_push_in_group(group_id)
        yield event.plain_result("✅ 在本群的股市新闻推送已开启！")


    @stock_group.command("关闭推送")
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def disable_push(self, event: AstrMessageEvent):
        """
        在本群关闭新闻推送。
        """
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("此功能仅限群聊使用。")
            return

        if group_id not in self.playing_groups:
            yield event.plain_result("本群的新闻推送尚未开启。")
            return

        await self.disable_push_in_group(group_id)
        yield event.plain_result("❌ 在本群的股市新闻推送已关闭。")

    @stock_group.command("开户")
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def join_game(self, event: AstrMessageEvent):
        """
        加入模拟炒股游戏。
        """
        user_name = event.get_sender_name()
        user_id = event.get_sender_id()
        group_id = event.get_group_id()

        if not group_id:  # 理论上会被 filter 拦住
            yield event.plain_result("开户失败，似乎无法在私聊中进行游戏。")
            return

        portfolio = await self.get_user_portfolio(user_id, group_id)

        if portfolio:
            yield event.plain_result(f"@{user_name} 您已经开户了。使用 /炒股 菜单 查看所有指令。")
            return

        new_portfolio = await self.create_user_portfolio(user_id, group_id)
        if new_portfolio:
            yield event.plain_result(
                f"@{user_name} 恭喜您开户成功！\n"
                f"获得启动资金: ${new_portfolio['cash']:.2f}\n"
                f"使用 /炒股 菜单 查看所有指令。\n"
                f"提示: 使用 /炒股 开启推送 可以在本群接收新闻！"
            )
        else:
            yield event.plain_result("开户失败，发生未知错误。")


    @stock_group.command("全球局势")
    async def get_global_news(self, event: AstrMessageEvent):
        """
        查看当前所有活跃的全球事件。
        """
        report = "--- 📰 全球局势报告 📰 ---\n\n"
        if not self.active_global_events:
            report += "目前市场风平浪静，暂无全球性事件影响。\n"
        else:
            report += "以下全球事件正在影响市场：\n\n"
            for e in self.active_global_events:
                impact_str = "利好" if e.get("trend_impact", 0) > 0 else "利空"
                impact_icon = "📈" if e.get("trend_impact", 0) > 0 else "📉"
                report += f"【{impact_str}】{impact_icon} {e['content']}\n"
                report += f"  (剩余时间: {e['remaining_ticks']} 轮刷新)\n\n"

        report += "------------------------\n"
        report += "提示：使用 /炒股 新闻 查看最新突发事件。"

        yield event.plain_result(report)


    @stock_group.command("新闻")
    async def get_local_news(self, event: AstrMessageEvent):
        """
        查看最新一条突发新闻。
        """
        report = f"--- 📰 最新突发新闻 📰 ---\n\n{self.last_local_event_news}\n\n"
        report += "------------------------\n"
        report += "提示：突发新闻是瞬时发生的，没有持续时间。"
        yield event.plain_result(report)

    @stock_group.command("大盘")
    async def view_market(self, event: AstrMessageEvent):
        """
        使用 HTML 渲染器查看股票大盘。
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
                        color_class = "color-red"  # 涨 (红)
                    elif change < 0:
                        change_str = f"↓ {change_percent:+.2f}%"
                        color_class = "color-green"  # 跌 (绿)
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
            img_url = await render_market_image(self, self.active_global_events, stocks_to_render)
            yield event.image_result(img_url)
        except Exception as e:
            logger.error(f"渲染大盘图片失败: {e}，回退到纯文本。")
            yield event.plain_result("渲染大盘图片失败，请检查后台日志。")

    @stock_group.command("详情")
    async def view_stock_detail(self, event: AstrMessageEvent, code: str):
        """
        (K线图) - 已修改为Matplotlib
        """
        code = code.upper()
        group_id = event.get_group_id()
        user_id = event.get_sender_id()

        # --- 新增：获取玩家个人持仓信息 ---
        portfolio = await self.get_user_portfolio(user_id, group_id)
        user_avg_buy_price: Optional[float] = None
        user_held_amount: int = 0

        if portfolio:
            user_stock_data = portfolio.get("stocks", {}).get(code)
            if user_stock_data:
                user_avg_buy_price = user_stock_data.get("avg_buy_price")
                user_held_amount = user_stock_data.get("amount", 0)
        # --- 新增结束 ---

        # 先在锁外获取持仓数据
        total_shares = 0
        if group_id:
            try:
                total_shares = await self.get_total_shares_in_group(group_id, code)
            except Exception as e:
                logger.error(f"获取群组 {group_id} 总持仓 {code} 失败: {e}", exc_info=True)
                # 即使失败也继续，只是不显示持仓量

        # 在锁内获取价格数据
        stock_info = None
        current_price = 0.0
        price_history = []
        price_color = "#6c757d"  # 默认灰色

        async with self.game_lock:
            stock_info = self.stocks_data.get(code)
            if not stock_info:
                yield event.plain_result(f"错误：未找到股票代码 {code}。")
                return

            current_price = self.stock_prices.get(code, 0.0)
            price_history = self.price_history.get(code, [])

            if len(price_history) < 2:
                yield event.plain_result(
                    f"【{code}】历史数据不足 (仅 {len(price_history)} 个数据点)，暂无法绘制K线图。请等待下一次市场刷新。")
                return

            if price_history[-1] > price_history[-2]:
                price_color = "#dc3545"  # 涨 (红色)
            elif price_history[-1] < price_history[-2]:
                price_color = "#28a745"  # 跌 (绿色)

        # 准备渲染数据
        render_data = {
            "stock_name": stock_info.get("name", "未知"),
            "stock_code": code,
            "current_price": f"{current_price:.2f}",
            "stock_industry": stock_info.get("industry", "未知"),
            "stock_tags": stock_info.get("tags", []),
            "price_data": price_history,
            "price_color": price_color,
            "total_shares": total_shares,  # 总持仓数据
            "group_id": group_id,  # 传递group_id用于图表标题

            # --- 新增：传入玩家持仓数据 ---
            "user_avg_buy_price": user_avg_buy_price,
            "user_held_amount": user_held_amount
        }

        # 调用Matplotlib渲染器
        try:
            # 假设 render_stock_detail_image_matplotlib 已经被更新以处理新的数据
            img_url = await render_stock_detail_image_matplotlib(self, render_data)
            yield event.image_result(img_url)
        except Exception as e:
            logger.error(f"渲染Matplotlib K线图 {code} 失败: {e}", exc_info=True)
            yield event.plain_result(f"渲染股票 {code} 的K线图时发生内部错误。")

    @stock_group.command("我的资产")
    async def view_portfolio(self, event: AstrMessageEvent):
        """
        查看自己的资产和持仓。
        """
        user_name = event.get_sender_name()
        user_id = event.get_sender_id()
        group_id = event.get_group_id()

        portfolio = await self.get_user_portfolio(user_id, group_id)

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
            total_cost_basis = 0.0  # 总成本

            if not holdings:
                report += "  (暂无持仓)\n"
            else:
                # 修改此循环以处理新数据结构
                for code, data in holdings.items():
                    amount = data.get("amount", 0)
                    if amount == 0:
                        continue  # 跳过无效数据

                    avg_buy_price = data.get("avg_buy_price", 0.0)

                    current_price = self.stock_prices.get(code, 0.0)
                    value = current_price * amount
                    total_stock_value += value

                    cost_basis = avg_buy_price * amount
                    total_cost_basis += cost_basis

                    profit_loss = value - cost_basis
                    profit_loss_percent = (profit_loss / cost_basis * 100) if cost_basis != 0 else 0

                    stock_name = self.stocks_data.get(code, {}).get("name", "???")
                    report += f"  - 【{code}】{stock_name}\n"
                    report += f"    持有: {amount} 股\n"
                    report += f"    均价: ${avg_buy_price:.2f}\n"  # 持仓均价
                    report += f"    市值: ${value:.2f} (@ ${current_price:.2f}/股)\n"

                    # 显示盈亏
                    if profit_loss > 0:
                        report += f"    盈亏: 📈 +${profit_loss:.2f} (+{profit_loss_percent:.2f}%)\n"
                    elif profit_loss < 0:
                        report += f"    盈亏: 📉 -${abs(profit_loss):.2f} ({profit_loss_percent:.2f}%)\n"
                    else:
                        report += f"    盈亏: ➖ ${profit_loss:.2f} (0.00%)\n"

            total_assets = cash + total_stock_value
            total_profit_loss = total_assets - (cash + total_cost_basis)  # 仅计算股票盈亏

            report += "\n------------------------\n"
            report += f"💳 总资产 (现金+市值): ${total_assets:.2f}\n"

            # 显示总盈亏
            if total_profit_loss > 0:
                report += f"📈 总盈亏: +${total_profit_loss:.2f}"
            elif total_profit_loss < 0:
                report += f"📉 总盈亏: -${abs(total_profit_loss):.2f}"
            else:
                report += f"➖ 总盈亏: $0.00"

            yield event.plain_result(report)

    @stock_group.command("买入")
    async def buy_stock(self, event: AstrMessageEvent, code: str, amount_str: str):
        """ 购买股票。"""
        user_name = event.get_sender_name()
        user_id = event.get_sender_id()
        group_id = event.get_group_id()

        portfolio = await self.get_user_portfolio(user_id, group_id)

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

            # --- 修改持仓数据结构 ---
            current_holdings = portfolio.get("stocks", {})
            stock_data = current_holdings.get(code, {"amount": 0, "avg_buy_price": 0.0})

            current_amount = stock_data.get("amount", 0)
            current_avg_price = stock_data.get("avg_buy_price", 0.0)

            # 计算新的加权平均价
            new_total_amount = current_amount + amount
            new_total_cost = (current_avg_price * current_amount) + total_cost
            new_avg_price = new_total_cost / new_total_amount

            current_holdings[code] = {
                "amount": new_total_amount,
                "avg_buy_price": new_avg_price
            }
            # --- 修改结束 ---

            portfolio["stocks"] = current_holdings

            await self.save_user_portfolio(user_id, group_id, portfolio)

            yield event.plain_result(
                f"@{user_name} 交易成功！\n"
                f"👍 **买入** {amount} 股 【{code}】\n"
                f"成交价: ${current_price:.2f}\n"
                f"花费: ${total_cost:.2f}\n"
                f"持仓均价: ${new_avg_price:.2f} (共 {new_total_amount} 股)\n"  # 显示新均价
                f"剩余现金: ${portfolio['cash']:.2f}"
            )


    @stock_group.command("卖出")
    async def sell_stock(self, event: AstrMessageEvent, code: str, amount_str: str):
        """ 卖出股票。 (无变化) """
        user_name = event.get_sender_name()
        user_id = event.get_sender_id()
        group_id = event.get_group_id()

        portfolio = await self.get_user_portfolio(user_id, group_id)

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

            # --- 修改持仓数据结构 ---
            stock_data = current_holdings.get(code)

            if not stock_data:
                yield event.plain_result(f"@{user_name} 您未持有 {code}。")
                return

            held_amount = stock_data.get("amount", 0)
            avg_buy_price = stock_data.get("avg_buy_price", 0.0)
            # --- 修改结束 ---

            if held_amount < amount:
                yield event.plain_result(f"@{user_name} 持仓不足！您只有 {held_amount} 股 {code}。")
                return

            current_price = self.stock_prices.get(code)
            if current_price is None:
                yield event.plain_result(f"股票代码 {code} 异常，无法交易。")
                return

            # 卖出总收入
            total_revenue = current_price * amount
            # 卖出部分成本
            cost_of_sold_shares = avg_buy_price * amount
            # 本次卖出盈亏
            sale_profit_loss = total_revenue - cost_of_sold_shares

            portfolio["cash"] = portfolio.get("cash", 0.0) + total_revenue

            # --- 修改持仓数据结构 ---
            new_amount = held_amount - amount
            if new_amount == 0:
                del current_holdings[code]
            else:
                # 平均买入价不变，只更新数量
                current_holdings[code]["amount"] = new_amount
            # --- 修改结束 ---

            portfolio["stocks"] = current_holdings

            await self.save_user_portfolio(user_id, group_id, portfolio)

            profit_loss_str = f"获利: ${sale_profit_loss:.2f}"
            if sale_profit_loss < 0:
                profit_loss_str = f"亏损: -${abs(sale_profit_loss):.2f}"
            elif sale_profit_loss == 0:
                profit_loss_str = f"盈亏: $0.00"

            yield event.plain_result(
                f"@{user_name} 交易成功！\n"
                f"👎 **卖出** {amount} 股 【{code}】\n"
                f"成交价: ${current_price:.2f}\n"
                f"收入: ${total_revenue:.2f}\n"
                f"({profit_loss_str})\n"  # 显示本次盈亏
                f"剩余现金: ${portfolio['cash']:.2f}"
            )


    @stock_group.command("全部卖出")
    async def sell_all_stocks(self, event: AstrMessageEvent):
        """ 卖出所有持仓股票。 """
        user_name = event.get_sender_name()
        user_id = event.get_sender_id()
        group_id = event.get_group_id()

        portfolio = await self.get_user_portfolio(user_id, group_id)

        if not portfolio:
            yield event.plain_result(f"@{user_name} 您尚未开户。")
            return

        holdings = portfolio.get("stocks", {})
        if not holdings:
            yield event.plain_result(f"@{user_name} 您没有任何持仓，无需卖出。")
            return

        total_revenue = 0.0
        total_cost_basis = 0.0
        report_lines = []

        async with self.game_lock:
            for code, data in holdings.items():
                amount = data.get("amount", 0)
                avg_buy_price = data.get("avg_buy_price", 0.0)
                if amount == 0:
                    continue

                current_price = self.stock_prices.get(code)
                if current_price is None:
                    report_lines.append(f"  - 【{code}】: 股票数据异常，无法卖出。")
                    continue

                revenue = current_price * amount
                cost_basis = avg_buy_price * amount
                profit_loss = revenue - cost_basis

                total_revenue += revenue
                total_cost_basis += cost_basis

                stock_name = self.stocks_data.get(code, {}).get("name", "???")
                pl_str = f"盈利 ${profit_loss:.2f}" if profit_loss >= 0 else f"亏损 -${abs(profit_loss):.2f}"
                report_lines.append(f"  - 【{code}】{stock_name}: 卖出 {amount} 股, {pl_str}")

        if not report_lines:
            yield event.plain_result(f"@{user_name} 持仓的股票均数据异常，无法卖出。")
            return

        # 更新资产
        portfolio["cash"] = portfolio.get("cash", 0.0) + total_revenue
        portfolio["stocks"] = {}  # 清空持仓

        await self.save_user_portfolio(user_id, group_id, portfolio)

        total_profit_loss = total_revenue - total_cost_basis
        pl_icon = "📈" if total_profit_loss > 0 else "📉" if total_profit_loss < 0 else "➖"

        report = f"@{user_name} 已全部卖出！\n\n"
        report += "--- 交易详情 ---\n"
        report += "\n".join(report_lines)
        report += "\n------------------\n"
        report += f"💰 总收入: ${total_revenue:.2f}\n"
        report += f"{pl_icon} 总盈亏: ${total_profit_loss:+.2f}\n"
        report += f"💳 剩余现金: ${portfolio['cash']:.2f}"

        yield event.plain_result(report)


    @stock_group.command("奖励资金")
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def admin_give_cash(self, event: AstrMessageEvent, amount_str: str):
        """ (管理员) 奖励资金 [金额] [@用户] """

        try:
            amount = float(amount_str)
            if amount <= 0:
                raise ValueError("金额必须为正数")
        except (ValueError, TypeError):
            yield event.plain_result("金额无效。用法: /炒股 奖励资金 10000 [@用户]")
            return

        # --- 修正处：根据文档遍历 event.message_obj.message ---
        mention_id: Optional[str] = None

        # 遍历消息链
        for component in event.message_obj.message:
            # 检查组件是否为 At (艾特) 类型
            if isinstance(component, Comp.At):
                # 假设 At 组件有 'qq' 属性来获取ID
                # (根据您提供的文档 Comp.At(qq=123456) 和项目中使用 NapCat 推断)
                try:
                    mention_id = str(component.qq)
                    break  # 找到第一个@就停止
                except AttributeError:
                    logger.warning(f"检测到 At 组件但无法获取 'qq' 属性: {component}")
        # --- 修正结束 ---

        if not mention_id:
            yield event.plain_result("请 @ 你要奖励的用户。用法: /炒股 奖励资金 10000 [@用户]")
            return

        target_user_id = mention_id
        group_id = event.get_group_id()
        if not group_id:
            return  # 理论上已被 filter 阻止

        target_portfolio = await self.get_user_portfolio(target_user_id, group_id)
        if not target_portfolio:
            yield event.plain_result(f"@{target_user_id} 该用户尚未开户，无法奖励。")
            return

        target_portfolio["cash"] = target_portfolio.get("cash", 0.0) + amount

        await self.save_user_portfolio(target_user_id, group_id, target_portfolio)

        admin_name = event.get_sender_name()
        yield event.plain_result(
            f"✅ 操作成功！\n"
            f"管理员 @{admin_name} 已向 @{target_user_id} 奖励资金 ${amount:.2f}。\n"
            f"该用户当前现金: ${target_portfolio['cash']:.2f}"
        )