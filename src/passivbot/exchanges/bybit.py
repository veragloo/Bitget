from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
from typing import Any

import websockets.exceptions

from passivbot.bot import Bot
from passivbot.datastructures import Candle
from passivbot.datastructures import Fill
from passivbot.datastructures import Order
from passivbot.datastructures import Position
from passivbot.datastructures import Tick
from passivbot.datastructures.config import NamedConfig
from passivbot.datastructures.runtime import RuntimeFuturesConfig
from passivbot.utils.funcs.pure import date_to_ts
from passivbot.utils.funcs.pure import ts_to_date
from passivbot.utils.httpclient import ByBitHTTPClient
from passivbot.utils.httpclient import HTTPRequestError
from passivbot.utils.procedures import log_async_exception

log = logging.getLogger(__name__)


def first_capitalized(s: str):
    return s[0].upper() + s[1:].lower()


def determine_pos_side(o: dict[str, Any]) -> str:
    if o["side"].lower() == "buy":
        if "entry" in o["order_link_id"]:
            return "long"
        elif "close" in o["order_link_id"]:
            return "short"
        else:
            return "both"
    else:
        if "entry" in o["order_link_id"]:
            return "short"
        elif "close" in o["order_link_id"]:
            return "long"
        else:
            return "both"


class Bybit(Bot):

    rtc: RuntimeFuturesConfig
    httpclient: ByBitHTTPClient

    def __bot_init__(self):
        """
        Subclass initialization routines
        """
        self.exchange = "bybit"

    @staticmethod
    def get_initial_runtime_config(config: NamedConfig) -> RuntimeFuturesConfig:
        return RuntimeFuturesConfig(
            market_type=config.market_type, short=config.short, long=config.long
        )

    @staticmethod
    async def get_exchange_info() -> dict[str, Any]:
        response: dict[str, Any] = await ByBitHTTPClient.onetime_get(
            "https://api.bybit.com/v2/public/symbols"
        )
        return response

    @staticmethod
    async def get_httpclient(config: NamedConfig) -> ByBitHTTPClient:
        if config.symbol.name.endswith("USDT"):
            endpoints = {
                "position": "/private/linear/position/list",
                "open_orders": "/private/linear/order/search",
                "create_order": "/private/linear/order/create",
                "cancel_order": "/private/linear/order/cancel",
                "ticks": "/public/linear/recent-trading-records",
                "fills": "/private/linear/trade/execution/list",
                "ohlcvs": "/public/linear/kline",
                "websocket_market": "wss://stream.bybit.com/realtime_public",
                "websocket_user": "wss://stream.bybit.com/realtime_private",
                "income": "/private/linear/trade/closed-pnl/list",
            }

        else:
            if config.symbol.name.endswith("USD"):
                endpoints = {
                    "position": "/v2/private/position/list",
                    "open_orders": "/v2/private/order",
                    "create_order": "/v2/private/order/create",
                    "cancel_order": "/v2/private/order/cancel",
                    "ticks": "/v2/public/trading-records",
                    "fills": "/v2/private/execution/list",
                    "ohlcvs": "/v2/public/kline/list",
                    "websocket_market": "wss://stream.bybit.com/realtime",
                    "websocket_user": "wss://stream.bybit.com/realtime",
                    "income": "/v2/private/trade/closed-pnl/list",
                }
            else:
                endpoints = {
                    "position": "/futures/private/position/list",
                    "open_orders": "/futures/private/order",
                    "create_order": "/futures/private/order/create",
                    "cancel_order": "/futures/private/order/cancel",
                    "ticks": "/v2/public/trading-records",
                    "fills": "/futures/private/execution/list",
                    "ohlcvs": "/v2/public/kline/list",
                    "websocket_market": "wss://stream.bybit.com/realtime",
                    "websocket_user": "wss://stream.bybit.com/realtime",
                    "income": "/futures/private/trade/closed-pnl/list",
                }

        endpoints.update(
            {
                "balance": "/v2/private/wallet/balance",
                "exchange_info": "/v2/public/symbols",
                "ticker": "/v2/public/tickers",
                "server_time": "/v2/public/time",
                "spot_balance": "/spot/v1/account",
                "balance": "/v2/private/wallet/balance",
                "exchange_info": "/v2/public/symbols",
                "ticker": "/v2/public/tickers",
            }
        )
        return ByBitHTTPClient(
            "https://api.bybit.com",
            config.api_key.key,
            config.api_key.secret,
            endpoints=endpoints,
        )

    @staticmethod
    async def init_market_type(config: NamedConfig, rtc: RuntimeFuturesConfig):  # type: ignore[override]
        if config.symbol.name.endswith("USDT"):
            log.info("linear perpetual")
            rtc.market_type += "_linear_perpetual"
            rtc.inverse = False
        else:
            rtc.inverse = True
            if config.symbol.name.endswith("USD"):
                log.info("inverse perpetual")
                rtc.market_type += "_inverse_perpetual"
                rtc.hedge_mode = False
            else:
                log.info("inverse futures")
                rtc.market_type += "_inverse_futures"

        exchange_info = await Bybit.get_exchange_info()
        results: list[dict[str, Any]] = exchange_info["result"]
        symbol_data: dict[str, Any] | None = None
        for symbol_data in results:
            if symbol_data["name"] == config.symbol.name:
                break
        else:
            raise Exception(f"symbol missing {config.symbol.name}")

        assert symbol_data

        rtc.max_leverage = symbol_data["leverage_filter"]["max_leverage"]
        rtc.coin = symbol_data["base_currency"]
        rtc.quote = symbol_data["quote_currency"]
        rtc.price_step = float(symbol_data["price_filter"]["tick_size"])
        rtc.qty_step = float(symbol_data["lot_size_filter"]["qty_step"])
        rtc.min_qty = float(symbol_data["lot_size_filter"]["min_trading_qty"])
        rtc.min_cost = 0.0
        if rtc.inverse:
            rtc.margin_coin = rtc.coin
        else:
            rtc.margin_coin = rtc.quote

    async def _init(self):
        self.httpclient = await self.get_httpclient(self.config)
        await self.init_market_type(self.config, self.rtc)
        await super()._init()
        await self.init_order_book()
        await self.update_position()

    async def init_order_book(self):
        ticker = await self.httpclient.get(
            "ticker", signed=True, params={"symbol": self.config.symbol.name}
        )
        self.ob = [float(ticker["result"][0]["bid_price"]), float(ticker["result"][0]["ask_price"])]
        self.rtc.price = float(ticker["result"][0]["last_price"])

    async def fetch_open_orders(self) -> list[Order]:
        fetched = await self.httpclient.get(
            "open_orders", signed=True, params={"symbol": self.config.symbol.name}
        )
        if self.rtc.inverse:
            created_at_key = "created_at"
        else:
            created_at_key = "created_time"
        return [
            Order.from_bybit_payload(elm, created_at_key=created_at_key)
            for elm in fetched["result"]
        ]

    async def fetch_position(self) -> Position:
        position: dict[str, Any] = {}
        if "linear_perpetual" in self.rtc.market_type:
            fetched, bal = await asyncio.gather(
                self.httpclient.get(
                    "position", signed=True, params={"symbol": self.config.symbol.name}
                ),
                self.httpclient.get("balance", signed=True, params={"coin": self.rtc.quote}),
            )
            long_pos = [e for e in fetched["result"] if e["side"] == "Buy"][0]
            short_pos = [e for e in fetched["result"] if e["side"] == "Sell"][0]
            position["wallet_balance"] = float(bal["result"][self.rtc.quote]["wallet_balance"])
        else:
            fetched, bal = await asyncio.gather(
                self.httpclient.get(
                    "position", signed=True, params={"symbol": self.config.symbol.name}
                ),
                self.httpclient.get("balance", signed=True, params={"coin": self.rtc.coin}),
            )
            position["wallet_balance"] = float(bal["result"][self.rtc.coin]["wallet_balance"])
            if "inverse_perpetual" in self.rtc.market_type:
                if fetched["result"]["side"] == "Buy":
                    long_pos = fetched["result"]
                    short_pos = {"size": 0.0, "entry_price": 0.0, "liq_price": 0.0}
                else:
                    long_pos = {"size": 0.0, "entry_price": 0.0, "liq_price": 0.0}
                    short_pos = fetched["result"]
            elif "inverse_futures" in self.rtc.market_type:
                long_pos = [e["data"] for e in fetched["result"] if e["data"]["position_idx"] == 1][
                    0
                ]
                short_pos = [
                    e["data"] for e in fetched["result"] if e["data"]["position_idx"] == 2
                ][0]
            else:
                raise Exception("unknown market type")

        position["long"] = {
            "size": float(long_pos["size"]),
            "price": float(long_pos["entry_price"]),
            "liquidation_price": float(long_pos["liq_price"]),
        }
        position["short"] = {
            "size": -float(short_pos["size"]),
            "price": float(short_pos["entry_price"]),
            "liquidation_price": float(short_pos["liq_price"]),
        }
        return Position.parse_obj(position)

    async def execute_order(self, order: Order) -> Order | None:
        o = None
        try:
            params = order.to_bybit_payload(
                market_type=self.rtc.market_type, hedge_mode=self.rtc.hedge_mode
            )
            o = await self.httpclient.post("create_order", params=params)
            if o["result"]:
                if self.rtc.inverse:
                    created_at_key = "created_at"
                else:
                    created_at_key = "created_time"
                return Order.from_bybit_payload(o["result"], created_at_key=created_at_key)
            return None
        except HTTPRequestError as exc:
            log.error("API Error code=%s; message=%s", exc.code, exc.msg)
        except Exception as e:
            log.error(f"error executing order {order} {e}", exc_info=True)
            log_async_exception(o)
        return None

    async def execute_cancellation(self, order: Order) -> Order | None:
        cancellation = None
        try:
            cancellation = await self.httpclient.post(
                "cancel_order",
                params={"symbol": self.config.symbol.name, "order_id": order.order_id},
            )
            order.order_id = cancellation["result"]["order_id"]
            return order
        except HTTPRequestError as exc:
            log.error("API Error code=%s; message=%s", exc.code, exc.msg)
        except Exception as e:
            log.error(f"error cancelling order {order} {e}", exc_info=True)
            log_async_exception(cancellation)
            self.ts_released["force_update"] = 0.0
        return None

    async def fetch_account(self):
        try:
            resp = await self.httpclient.get("spot_balance", signed=True)
            return resp["result"]
        except HTTPRequestError as exc:
            log.error("API Error code=%s; message=%s", exc.code, exc.msg)
        except Exception as e:
            log.error("error fetching account: %s", e)
        return {"balances": []}

    async def fetch_ticks(self, from_id: int | None = None, do_print: bool = True):
        params = {"symbol": self.config.symbol.name, "limit": 1000}
        if from_id is not None:
            params["from"] = max(0, from_id)
        try:
            ticks = await self.httpclient.get("ticks", params=params)
        except HTTPRequestError as exc:
            log.error("API Error code=%s; message=%s", exc.code, exc.msg)
            return []
        except Exception as e:
            log.error("error fetching ticks: %s", e)
            return []
        try:
            trades = [Tick.from_bybit_payload(tick) for tick in ticks["result"]]
            if do_print:
                log.info(
                    "fetched trades %s %s %s",
                    self.config.symbol.name,
                    trades[0].trade_id,
                    ts_to_date(float(trades[0].timestamp) / 1000),
                )
        except Exception:
            trades = []
            if do_print:
                log.info("fetched no new trades %s", self.config.symbol.name)
        return trades

    async def fetch_ohlcvs(
        self, start_time: int | None = None, interval="1m", limit=200
    ) -> list[Candle]:
        # m -> minutes; h -> hours; d -> days; w -> weeks; M -> months
        interval_map = {
            "1m": 1,
            "3m": 3,
            "5m": 5,
            "15m": 15,
            "30m": 30,
            "1h": 60,
            "2h": 120,
            "4h": 240,
            "6h": 360,
            "12h": 720,
            "1d": "D",
            "1w": "W",
            "1M": "M",
        }
        assert interval in interval_map
        params = {
            "symbol": self.config.symbol.name,
            "interval": interval_map[interval],
            "limit": limit,
        }
        if start_time is None:
            server_time = await self.httpclient.get("server_time")
            mapped_interval: int = interval_map[interval]  # type: ignore[assignment]
            if isinstance(mapped_interval, str):
                mapped_minutes: int = {"D": 1, "W": 7, "M": 30}[mapped_interval]
                minutes = mapped_minutes * 60 * 24
            else:
                minutes = mapped_interval
            params["from"] = int(round(float(server_time["time_now"]))) - 60 * minutes * limit
        else:
            params["from"] = int(start_time / 1000)
        fetched: list[dict[str, Any]] = await self.httpclient.get("ohlcvs", params=params)  # type: ignore[assignment]
        candles: list[Candle] = []
        for e in fetched:
            e["timestamp"] = e.pop("open_time") * 1000
            candles.append(Candle.parse_obj(e))
        return candles

    async def get_all_income(
        self,
        symbol: str | None = None,
        start_time: int | None = None,
        income_type: str = "Trade",
        end_time: int | None = None,
    ):
        limit = 50
        income: list[dict[str, Any]] = []
        page = 1
        while True:
            fetched = await self.fetch_income(
                symbol=symbol,
                start_time=start_time,
                income_type=income_type,
                limit=limit,
                page=page,
            )
            if len(fetched) == 0:
                break
            log.info("fetched income %s", ts_to_date(fetched[0]["timestamp"]))
            if fetched == income[-len(fetched) :]:
                break
            income += fetched
            if len(fetched) < limit:
                break
            page += 1
        income_d = {e["transaction_id"]: e for e in income}
        return sorted(income_d.values(), key=lambda x: x["timestamp"])  # type: ignore[no-any-return]

    async def fetch_income(
        self,
        symbol: str | None = None,
        income_type: str | None = None,
        limit: int = 50,
        start_time: int | None = None,
        end_time: int | None = None,
        page: int | None = None,
    ):
        params = {"limit": limit, "symbol": self.config.symbol.name if symbol is None else symbol}
        if start_time is not None:
            params["start_time"] = int(start_time / 1000)
        if end_time is not None:
            params["end_time"] = int(end_time / 1000)
        if income_type is not None:
            params["exec_type"] = first_capitalized(income_type)
        if page is not None:
            params["page"] = page
        fetched = None
        try:
            fetched = await self.httpclient.get("income", signed=True, params=params)
            if fetched["result"]["data"] is None:
                return []
            return sorted(
                (
                    {
                        "symbol": e["symbol"],
                        "income_type": e["exec_type"].lower(),
                        "income": float(e["closed_pnl"]),
                        "token": self.rtc.margin_coin,
                        "timestamp": float(e["created_at"]) * 1000,
                        "info": {"page": fetched["result"]["current_page"]},
                        "transaction_id": float(e["id"]),
                        "trade_id": e["order_id"],
                    }
                    for e in fetched["result"]["data"]
                ),
                key=lambda x: x["timestamp"],  # type: ignore[no-any-return]
            )
        except HTTPRequestError as exc:
            log.error("API Error code=%s; message=%s", exc.code, exc.msg)
        except Exception as e:
            log.error("error fetching income: %s", e, exc_info=True)
            log_async_exception(fetched)
        return []

    async def fetch_fills(
        self,
        symbol: str | None = None,
        limit: int = 200,
        from_id: int | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[Fill]:
        return []

    #        ffills, fpnls = await asyncio.gather(
    #            self.httpclient.get("fills", signed=True, params={"symbol": self.config.symbol.name, "limit": limit}),
    #            self.httpclient.get("pnls", signed=True, params={"symbol": self.config.symbol.name, "limit": 50}),
    #        )
    #        return ffills, fpnls
    #        try:
    #            fills = []
    #            for x in fetched["result"]["data"][::-1]:
    #                qty, price = float(x["order_qty"]), float(x["price"])
    #                if not qty or not price:
    #                    continue
    #                fill = {
    #                    "symbol": x["symbol"],
    #                    "id": str(x["exec_id"]),
    #                    "order_id": str(x["order_id"]),
    #                    "side": x["side"].lower(),
    #                    "price": price,
    #                    "qty": qty,
    #                    "realized_pnl": 0.0,
    #                    "cost": (cost := qty / price if self.rtc.inverse else qty * price),
    #                    "fee_paid": float(x["exec_fee"]),
    #                    "fee_token": self.rtc.margin_coin,
    #                    "timestamp": int(x["trade_time_ms"]),
    #                    "position_side": determine_pos_side(x),
    #                    "is_maker": x["fee_rate"] < 0.0,
    #                }
    #                fills.append(fill)
    #            return fills
    #        except Exception as e:
    #            log.info("error fetching fills", e)
    #            return []
    #        log.info("ntufnt")
    #        return fetched
    #        log.info("fetch_fills not implemented for Bybit")
    #        return []

    async def init_exchange_config(self):
        try:
            # set cross mode
            if "inverse_futures" in self.rtc.market_type:
                try:
                    await self.httpclient.post(
                        "/futures/private/position/leverage/save",
                        params={
                            "symbol": self.config.symbol.name,
                            "position_idx": 1,
                            "buy_leverage": 0,
                            "sell_leverage": 0,
                        },
                    )
                except HTTPRequestError as exc:
                    if exc.code != 130056:
                        raise
                    log.info(exc.msg)
                try:
                    await self.httpclient.post(
                        "/futures/private/position/leverage/save",
                        params={
                            "symbol": self.config.symbol.name,
                            "position_idx": 2,
                            "buy_leverage": 0,
                            "sell_leverage": 0,
                        },
                    )
                except HTTPRequestError as exc:
                    if exc.code != 130056:
                        raise
                    log.info(exc.msg)
                try:
                    await self.httpclient.post(
                        "/futures/private/position/switch-mode",
                        params={"symbol": self.config.symbol.name, "mode": 3},
                    )
                except HTTPRequestError as exc:
                    if exc.code != 130056:
                        raise
                    log.info(exc.msg)
            elif "linear_perpetual" in self.rtc.market_type:
                try:
                    await self.httpclient.post(
                        "/private/linear/position/switch-isolated",
                        params={
                            "symbol": self.config.symbol.name,
                            "is_isolated": False,
                            "buy_leverage": 7,
                            "sell_leverage": 7,
                        },
                    )
                except HTTPRequestError as exc:
                    if exc.code != 130056:
                        raise
                    log.info(exc.msg)
                try:
                    await self.httpclient.post(
                        "/private/linear/position/set-leverage",
                        params={
                            "symbol": self.config.symbol.name,
                            "buy_leverage": 7,
                            "sell_leverage": 7,
                        },
                    )
                except HTTPRequestError as exc:
                    if exc.code != 34036:
                        raise
                    log.info(exc.msg)
            elif "inverse_perpetual" in self.rtc.market_type:
                try:
                    await self.httpclient.post(
                        "/v2/private/position/leverage/save",
                        params={"symbol": self.config.symbol.name, "leverage": 0},
                    )
                except HTTPRequestError as exc:
                    if exc.code != 130056:
                        raise
                    log.info(exc.msg)

        except Exception as e:
            log.error("Error in init_exchange_config: %s", e, exc_info=True)

    def standardize_market_stream_event(self, data: dict[str, Any]) -> list[Tick]:
        ticks = []
        for e in data["data"]:
            try:
                ticks.append(Tick.from_bybit_payload(e))
            except Exception as exc:
                log.error("error in websocket tick %s: %s", e, exc, exc_info=True)
        return ticks

    async def beat_heart_user_stream(self, ws) -> None:
        while True:
            await asyncio.sleep(27)
            try:
                await ws.send(json.dumps({"op": "ping"}))
            except websockets.exceptions.ConnectionClosedOK:
                break
            except Exception as e:
                log.error("error sending heartbeat: %s", e, exc_info=True)

    async def subscribe_to_market_stream(self, ws):
        await ws.send(json.dumps({"op": "subscribe", "args": ["trade." + self.config.symbol.name]}))

    async def subscribe_to_user_stream(self, ws):
        expires = int((time.time() + 1) * 1000)
        signature = str(
            hmac.new(
                bytes(self.config.api_key.secret, "utf-8"),
                bytes(f"GET/realtime{expires}", "utf-8"),
                digestmod="sha256",
            ).hexdigest()
        )
        await ws.send(
            json.dumps({"op": "auth", "args": [self.config.api_key.key, expires, signature]})
        )
        await asyncio.sleep(1)
        await ws.send(
            json.dumps({"op": "subscribe", "args": ["position", "execution", "wallet", "order"]})
        )

    async def transfer(self, type_: str, amount: float, asset: str = "USDT"):
        return {"code": "-1", "msg": "Transferring funds not supported for Bybit"}

    def standardize_user_stream_event(self, event: dict[str, Any]) -> dict[str, Any]:
        standardized: dict[str, Any] = {}
        if "topic" in event:
            if event["topic"] == "order":
                for elm in event["data"]:
                    if elm["symbol"] == self.config.symbol.name:
                        if elm["order_status"] == "Created":
                            pass
                        elif elm["order_status"] == "Rejected":
                            pass
                        elif elm["order_status"] == "New":
                            if self.rtc.inverse:
                                timestamp = date_to_ts(elm["timestamp"])
                            else:
                                timestamp = date_to_ts(elm["update_time"])
                            new_open_order = {
                                "order_id": elm["order_id"],
                                "symbol": elm["symbol"],
                                "price": float(elm["price"]),
                                "qty": float(elm["qty"]),
                                "type": elm["order_type"].lower(),
                                "side": elm["side"].lower(),
                                "timestamp": timestamp,
                            }
                            if "inverse_perpetual" in self.rtc.market_type:
                                if self.position.long.size == 0.0:
                                    if self.position.short.size == 0.0:
                                        new_open_order["position_side"] = (
                                            "long" if new_open_order["side"] == "buy" else "short"
                                        )
                                    else:
                                        new_open_order["position_side"] = "short"
                                else:
                                    new_open_order["position_side"] = "long"
                            elif "inverse_futures" in self.rtc.market_type:
                                new_open_order["position_side"] = determine_pos_side(elm)
                            else:
                                new_open_order["position_side"] = (
                                    "long"
                                    if (
                                        (
                                            new_open_order["side"] == "buy"
                                            and elm["create_type"] == "CreateByUser"
                                        )
                                        or (
                                            new_open_order["side"] == "sell"
                                            and elm["create_type"] == "CreateByClosing"
                                        )
                                    )
                                    else "short"
                                )
                            standardized["new_open_order"] = new_open_order
                        elif elm["order_status"] == "PartiallyFilled":
                            standardized["deleted_order_id"] = standardized[
                                "partially_filled"
                            ] = elm["order_id"]
                        elif elm["order_status"] == "Filled":
                            standardized["deleted_order_id"] = elm["order_id"]
                        elif elm["order_status"] == "Cancelled":
                            standardized["deleted_order_id"] = elm["order_id"]
                        elif elm["order_status"] == "PendingCancel":
                            pass
                    else:
                        standardized["other_symbol"] = elm["symbol"]
                        standardized["other_type"] = event["topic"]
            elif event["topic"] == "execution":
                for elm in event["data"]:
                    if elm["symbol"] == self.config.symbol.name:
                        if elm["exec_type"] == "Trade":
                            # already handled by "order"
                            pass
                    else:
                        standardized["other_symbol"] = elm["symbol"]
                        standardized["other_type"] = event["topic"]
            elif event["topic"] == "position":
                for elm in event["data"]:
                    if elm["symbol"] == self.config.symbol.name:
                        if elm["side"] == "Buy":
                            standardized["long_psize"] = float(elm["size"])
                            standardized["long_pprice"] = float(elm["entry_price"])
                        elif elm["side"] == "Sell":
                            standardized["short_psize"] = -abs(float(elm["size"]))
                            standardized["short_pprice"] = float(elm["entry_price"])
                        if self.rtc.inverse:
                            standardized["wallet_balance"] = float(elm["wallet_balance"])
                    else:
                        standardized["other_symbol"] = elm["symbol"]
                        standardized["other_type"] = event["topic"]
            elif not self.rtc.inverse and event["topic"] == "wallet":
                for elm in event["data"]:
                    standardized["wallet_balance"] = float(elm["wallet_balance"])
        return standardized
