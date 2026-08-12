import sys
import os
import datetime
import threading
import json
import re
import pandas as pd
import tkinter as tk
import inspect
from tkinter import ttk, scrolledtext, filedialog, messagebox

from fubon_neo.sdk import FubonSDK, FutOptOrder, Order, Condition, Mode, FutOptConditionOrder
from fubon_neo.constant import (
        BSAction,          # 買賣別 (Buy, Sell)
        OrderType,         # 證券交易種類 (Stock 現股, Margin 融資, Short 融券)
        PriceType,         # 證券價格種類 (Limit 限價, Market 市價)
        MarketType,        # 證券盤別 (Common 整股, Odd 零股)
        TimeInForce,       # 有效條件 (ROD, IOC, FOK)
        FutOptOrderType,   # 期權委託種類 (Auto)
        FutOptPriceType,   # 期權價格種類 (Limit, Market)
        FutOptMarketType,  # 期權盤別 (Future, FutureNight, Option, OptionNight)
        Operator,
        Direction,
        StopSign,
        TriggerContent,
        TradingType,
        FutOptConditionOrderType,
        FutOptConditionPriceType,
        FutOptConditionMarketType
    )
HAS_SDK = True

def clean_taiwan_char(text):
    """將所有『臺』統一清洗成『台』，確保模糊搜尋無死角"""
    if not isinstance(text, str):
        return str(text)
    return text.replace('臺', '台')

def parse_fubon_string(content_str):
    if not content_str or not isinstance(content_str, str):
        return content_str
    try:
        return json.loads(content_str)
    except Exception:
        pass
    kv_pairs = re.findall(r'(\w+):\s*["\']?([^"\',}]+)["\']?', content_str)
    if kv_pairs:
        return {
            k: (float(v.strip()) if '.' in v else int(v.strip())) 
            if v.strip().replace('.', '', 1).isdigit() else v.strip() 
            for k, v in kv_pairs
        }
    return {"raw_data": content_str}

def to_clean_list(obj):
    if obj is None:
        return []
    if hasattr(obj, "data"):
        data_source = getattr(obj, "data", [])
    else:
        data_source = obj if isinstance(obj, list) else [obj]
        
    cleaned = []
    for item in data_source:
        if hasattr(item, "__dict__"):
            item_dict = {
                k: to_clean_list(v) if hasattr(v, "__dict__") or isinstance(v, list) else v 
                for k, v in item.__dict__.items() if not k.startswith('_')
            }
            cleaned.append(item_dict)
        else:
            cleaned.append(parse_fubon_string(str(item)))
    return cleaned

def create_fubon_condition(
    symbol,
    trigger_price,
    trigger_dir,
    is_stock=False,
    is_after_hours=False
):

    cond = Condition(
        market_type=TradingType.Reference,
        symbol=symbol,
        trigger=TriggerContent.MatchedPrice,
        trigger_value=str(trigger_price),
        comparison=trigger_dir
    )

    return cond, "SDK v5"

    
class FullFeaturedAPITesterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("富邦 API 全功能交易與帳號工作站 (證券期貨自動分流與 WebSocket 退訂修正版)")
        self.root.geometry("1150x900")

        self.sdk = None
        self.ws = None
        self.current_mode = None
        self.accounts = []

        # 商品檔快取記憶體
        self.cache_stocks_df = None
        self.cache_futopt_df = None
        self.current_search_results_df = None

        # 行情 WebSocket 訂閱紀錄
        self.sub_id_map = {}
        self.sub_key_map = {}

        self._build_ui()

    def is_after_hours(self):
        """自動判斷當前是否處於夜盤時段 (15:00 ~ 05:00)"""
        current_time = datetime.datetime.now().time()
        return current_time >= datetime.time(15, 0) or current_time < datetime.time(5, 0)

    def _build_ui(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="x", padx=10, pady=5)

        # 1. 登入與帳號
        self.tab_login = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.tab_login, text=" 1. 登入與帳號 ")
        self._build_tab_login()

        # 2. 真實商品查詢
        self.tab_symbol = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.tab_symbol, text=" 2. 商品代碼查詢 ")
        self._build_tab_symbol()

        # 3. 綜合下單 (單式/複式/條件單)
        self.tab_order = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.tab_order, text=" 3. 綜合下單與條件單 ")
        self._build_tab_order()

        # 4. 行情 WebSocket
        self.tab_ws = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.tab_ws, text=" 4. 行情 WebSocket ")
        self._build_tab_ws()

        # 5. 帳務與部位查詢
        self.tab_accounting = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.tab_accounting, text=" 5. 帳務與部位查詢 ")
        self._build_tab_accounting()

        # 底部 Log 視窗
        log_frame = ttk.LabelFrame(self.root, text=" 全域 API 原始 Log 與回報視窗 ", padding=10)
        log_frame.pack(fill="both", expand=True, padx=10, pady=5)

        btn_clear = ttk.Button(log_frame, text="清空 Log", command=self.clear_log)
        btn_clear.pack(anchor="e", pady=(0, 5))

        self.log_area = scrolledtext.ScrolledText(
            log_frame, wrap="word", font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4"
        )
        self.log_area.pack(fill="both", expand=True)
  
    # ------------------------------------------------------------------
    # 分頁 1：登入與帳號
    # ------------------------------------------------------------------
    def _build_tab_login(self):
        f = self.tab_login

        ttk.Label(f, text="身分證字號:").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.entry_id = ttk.Entry(f, width=15)
        self.entry_id.grid(row=0, column=1, sticky="w", padx=5, pady=5)

        ttk.Label(f, text="登入密碼:").grid(row=0, column=2, sticky="w", padx=5, pady=5)
        self.entry_pwd = ttk.Entry(f, show="*", width=15)
        self.entry_pwd.grid(row=0, column=3, sticky="w", padx=5, pady=5)

        ttk.Label(f, text="憑證路徑:").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        self.entry_cert_path = ttk.Entry(f, width=45)
        self.entry_cert_path.grid(row=1, column=1, columnspan=2, sticky="we", padx=5, pady=5)
        
        btn_browse = ttk.Button(f, text="瀏覽...", width=8, command=self.browse_cert_file)
        btn_browse.grid(row=1, column=3, sticky="w", padx=5, pady=5)

        ttk.Label(f, text="憑證密碼:").grid(row=2, column=0, sticky="w", padx=5, pady=5)
        self.entry_cert_pwd = ttk.Entry(f, show="*", width=15)
        self.entry_cert_pwd.grid(row=2, column=1, sticky="w", padx=5, pady=5)

        btn_login = ttk.Button(f, text="執行 SDK 登入", command=self.action_login)
        btn_login.grid(row=3, column=0, columnspan=2, sticky="w", padx=5, pady=10)

    # ------------------------------------------------------------------
    # 分頁 2：商品查詢 UI
    # ------------------------------------------------------------------
    def _build_tab_symbol(self):
        f = self.tab_symbol

        search_frame = ttk.Frame(f)
        search_frame.pack(fill="x", padx=5, pady=5)

        ttk.Label(search_frame, text="市場別:").pack(side="left", padx=5)
        self.combo_search_market = ttk.Combobox(search_frame, values=["期權 (FutOpt)", "證券 (Stock)"], width=12, state="readonly")
        self.combo_search_market.current(0)
        self.combo_search_market.pack(side="left", padx=5)

        ttk.Label(search_frame, text="關鍵字:").pack(side="left", padx=5)
        self.entry_search_keyword = ttk.Entry(search_frame, width=20)
        self.entry_search_keyword.insert(0, "台指")
        self.entry_search_keyword.pack(side="left", padx=5)

        btn_search = ttk.Button(search_frame, text="🔍 搜尋商品", command=self.action_search_symbol)
        btn_search.pack(side="left", padx=5)

        btn_copy = ttk.Button(search_frame, text="📋 複製選取代碼", command=self.copy_selected_symbol)
        btn_copy.pack(side="left", padx=10)

        btn_export = ttk.Button(search_frame, text="📥 匯出結果 CSV", command=self.action_export_csv)
        btn_export.pack(side="left", padx=5)

        self.lbl_search_count = ttk.Label(f, text="提示: 請輸入關鍵字並點擊搜尋", foreground="#007acc")
        self.lbl_search_count.pack(anchor="w", padx=5, pady=2)

        tree_frame = ttk.Frame(f)
        tree_frame.pack(fill="both", expand=True, padx=5, pady=5)

        self.symbol_tree = ttk.Treeview(tree_frame, show="headings", height=8)
        self._setup_tree_columns_for_futopt()

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.symbol_tree.yview)
        self.symbol_tree.configure(yscroll=scrollbar.set)

        self.symbol_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _setup_tree_columns_for_futopt(self):
        self.symbol_tree["columns"] = ("symbol", "name", "deliveryMonth", "exchange")
        self.symbol_tree.heading("symbol", text="商品完整代碼")
        self.symbol_tree.heading("name", text="合約名稱")
        self.symbol_tree.heading("deliveryMonth", text="到期年月")
        self.symbol_tree.heading("exchange", text="交易所")

        self.symbol_tree.column("symbol", width=180)
        self.symbol_tree.column("name", width=250)
        self.symbol_tree.column("deliveryMonth", width=120)
        self.symbol_tree.column("exchange", width=100)

    def _setup_tree_columns_for_stock(self):
        self.symbol_tree["columns"] = ("symbol", "name", "market", "type")
        self.symbol_tree.heading("symbol", text="股票代碼")
        self.symbol_tree.heading("name", text="股票簡稱")
        self.symbol_tree.heading("market", text="市場別")
        self.symbol_tree.heading("type", text="商品類別")

        self.symbol_tree.column("symbol", width=150)
        self.symbol_tree.column("name", width=250)
        self.symbol_tree.column("market", width=120)
        self.symbol_tree.column("type", width=120)

    # ------------------------------------------------------------------
    # 分頁 3：綜合下單 (單式/複式/條件單)
    # ------------------------------------------------------------------
    def _build_tab_order(self):
        f = self.tab_order

        acc_frame = ttk.Frame(f)
        acc_frame.pack(fill="x", padx=5, pady=5)
        ttk.Label(acc_frame, text="選擇下單帳號:").pack(side="left", padx=5)
        self.combo_accounts = ttk.Combobox(acc_frame, width=45, state="readonly")
        self.combo_accounts.pack(side="left", padx=5)

        self.order_notebook = ttk.Notebook(f)
        self.order_notebook.pack(fill="x", padx=5, pady=5)

        self.tab_single_order = ttk.Frame(self.order_notebook, padding=10)
        self.order_notebook.add(self.tab_single_order, text=" 單式單下單 ")
        self._build_subtab_single_order()

        self.tab_combo_order = ttk.Frame(self.order_notebook, padding=10)
        self.order_notebook.add(self.tab_combo_order, text=" 複式單下單 (期權) ")
        self._build_subtab_combo_order()

        self.tab_cond_order = ttk.Frame(self.order_notebook, padding=10)
        self.order_notebook.add(self.tab_cond_order, text=" 雲端條件單 (期權觸價/停損) ")
        self._build_subtab_cond_order()

    def _build_subtab_single_order(self):
        f = self.tab_single_order

        ttk.Label(f, text="商品代碼:").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.entry_single_symbol = ttk.Entry(f, width=15)
        self.entry_single_symbol.insert(0, "2330")
        self.entry_single_symbol.grid(row=0, column=1, sticky="w", padx=5, pady=5)

        ttk.Label(f, text="買賣別:").grid(row=0, column=2, sticky="w", padx=5, pady=5)
        self.combo_single_action = ttk.Combobox(f, values=["Buy (買進)", "Sell (賣出)"], width=12, state="readonly")
        self.combo_single_action.current(0)
        self.combo_single_action.grid(row=0, column=3, sticky="w", padx=5, pady=5)

        ttk.Label(f, text="委託價格:").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        self.entry_single_price = ttk.Entry(f, width=15)
        self.entry_single_price.insert(0, "1000")
        self.entry_single_price.grid(row=1, column=1, sticky="w", padx=5, pady=5)

        ttk.Label(f, text="委託數量/張口:").grid(row=1, column=2, sticky="w", padx=5, pady=5)
        self.entry_single_qty = ttk.Entry(f, width=10)
        self.entry_single_qty.insert(0, "1")
        self.entry_single_qty.grid(row=1, column=3, sticky="w", padx=5, pady=5)

        ttk.Label(f, text="價格類型:").grid(row=2, column=0, sticky="w", padx=5, pady=5)
        self.combo_single_price_type = ttk.Combobox(f, values=["Limit (限價)", "Market (市價)"], width=12, state="readonly")
        self.combo_single_price_type.current(0)
        self.combo_single_price_type.grid(row=2, column=1, sticky="w", padx=5, pady=5)

        ttk.Label(f, text="委託條件:").grid(row=2, column=2, sticky="w", padx=5, pady=5)
        self.combo_single_tif = ttk.Combobox(f, values=["ROD", "IOC", "FOK"], width=10, state="readonly")
        self.combo_single_tif.current(0)
        self.combo_single_tif.grid(row=2, column=3, sticky="w", padx=5, pady=5)

        btn_send_single = ttk.Button(f, text="🚀 發送單式委託 (自動識別證券/期權)", command=self.action_send_single_order)
        btn_send_single.grid(row=3, column=0, columnspan=3, sticky="w", padx=5, pady=10)

    def _build_subtab_combo_order(self):
        f = self.tab_combo_order

        ttk.Label(f, text="[腳位1] 代碼:").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.entry_leg1_symbol = ttk.Entry(f, width=12)
        self.entry_leg1_symbol.insert(0, "TMFH6")
        self.entry_leg1_symbol.grid(row=0, column=1, sticky="w", padx=5, pady=5)

        ttk.Label(f, text="買賣別:").grid(row=0, column=2, sticky="w", padx=5, pady=5)
        self.combo_leg1_action = ttk.Combobox(f, values=["Buy (買進)", "Sell (賣出)"], width=12, state="readonly")
        self.combo_leg1_action.current(1)
        self.combo_leg1_action.grid(row=0, column=3, sticky="w", padx=5, pady=5)

        ttk.Label(f, text="[腳位2] 代碼:").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        self.entry_leg2_symbol = ttk.Entry(f, width=12)
        self.entry_leg2_symbol.insert(0, "TMFI6")
        self.entry_leg2_symbol.grid(row=1, column=1, sticky="w", padx=5, pady=5)

        ttk.Label(f, text="買賣別:").grid(row=1, column=2, sticky="w", padx=5, pady=5)
        self.combo_leg2_action = ttk.Combobox(f, values=["Buy (買進)", "Sell (賣出)"], width=12, state="readonly")
        self.combo_leg2_action.current(0)
        self.combo_leg2_action.grid(row=1, column=3, sticky="w", padx=5, pady=5)

        ttk.Label(f, text="價差/組合價格:").grid(row=2, column=0, sticky="w", padx=5, pady=5)
        self.entry_combo_price = ttk.Entry(f, width=12)
        self.entry_combo_price.insert(0, "-150")
        self.entry_combo_price.grid(row=2, column=1, sticky="w", padx=5, pady=5)

        ttk.Label(f, text="委託口數:").grid(row=2, column=2, sticky="w", padx=5, pady=5)
        self.entry_combo_qty = ttk.Entry(f, width=8)
        self.entry_combo_qty.insert(0, "1")
        self.entry_combo_qty.grid(row=2, column=3, sticky="w", padx=5, pady=5)

        btn_send_combo = ttk.Button(f, text="🚀 發送複式單委託", command=self.action_send_combo_order)
        btn_send_combo.grid(row=3, column=0, columnspan=2, sticky="w", padx=5, pady=10)

    def _build_subtab_cond_order(self):
        f = self.tab_cond_order

        # 自動生成預設日期字串 (今天 與 14天後)
        today_str = datetime.date.today().strftime('%Y-%m-%d')
        end_str = (datetime.date.today() + datetime.timedelta(days=14)).strftime('%Y-%m-%d')

        ttk.Label(f, text="觸發標的 Symbol:").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.entry_cond_symbol = ttk.Entry(f, width=12)
        self.entry_cond_symbol.insert(0, "TMFH6")
        self.entry_cond_symbol.grid(row=0, column=1, sticky="w", padx=5, pady=5)

        ttk.Label(f, text="觸發條件 (Direction):").grid(row=0, column=2, sticky="w", padx=5, pady=5)
        self.combo_cond_direction = ttk.Combobox(f, values=["GTE (>= 突破/大於等於)", "LTE (<= 跌破/小於等於)"], width=22, state="readonly")
        self.combo_cond_direction.current(0)
        self.combo_cond_direction.grid(row=0, column=3, sticky="w", padx=5, pady=5)

        ttk.Label(f, text="觸發價格 (Trigger Price):").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        self.entry_cond_trigger_price = ttk.Entry(f, width=12)
        self.entry_cond_trigger_price.insert(0, "20500")
        self.entry_cond_trigger_price.grid(row=1, column=1, sticky="w", padx=5, pady=5)

        ttk.Label(f, text="下單買賣別:").grid(row=1, column=2, sticky="w", padx=5, pady=5)
        self.combo_cond_action = ttk.Combobox(f, values=["Buy (買進)", "Sell (賣出)"], width=12, state="readonly")
        self.combo_cond_action.current(0)
        self.combo_cond_action.grid(row=1, column=3, sticky="w", padx=5, pady=5)

        ttk.Label(f, text="委託下單價格:").grid(row=2, column=0, sticky="w", padx=5, pady=5)
        self.entry_cond_order_price = ttk.Entry(f, width=12)
        self.entry_cond_order_price.insert(0, "20505")
        self.entry_cond_order_price.grid(row=2, column=1, sticky="w", padx=5, pady=5)

        ttk.Label(f, text="委託口數:").grid(row=2, column=2, sticky="w", padx=5, pady=5)
        self.entry_cond_qty = ttk.Entry(f, width=8)
        self.entry_cond_qty.insert(0, "1")
        self.entry_cond_qty.grid(row=2, column=3, sticky="w", padx=5, pady=5)

        # 💡 介面新增：條件單有效期限選擇框
        ttk.Label(f, text="條件開始日期:").grid(row=3, column=0, sticky="w", padx=5, pady=5)
        self.entry_cond_start_date = ttk.Entry(f, width=12)
        self.entry_cond_start_date.insert(0, today_str)
        self.entry_cond_start_date.grid(row=3, column=1, sticky="w", padx=5, pady=5)

        ttk.Label(f, text="條件結束日期:").grid(row=3, column=2, sticky="w", padx=5, pady=5)
        self.entry_cond_end_date = ttk.Entry(f, width=12)
        self.entry_cond_end_date.insert(0, end_str)
        self.entry_cond_end_date.grid(row=3, column=3, sticky="w", padx=5, pady=5)

        btn_send_cond = ttk.Button(f, text="🚀 送出雲端條件單", command=self.action_send_cond_order)
        btn_send_cond.grid(row=4, column=0, columnspan=2, sticky="w", padx=5, pady=10)

    # ------------------------------------------------------------------
    # 分頁 4：行情 WebSocket UI
    # ------------------------------------------------------------------
    def _build_tab_ws(self):
        f = self.tab_ws

        mode_frame = ttk.LabelFrame(f, text=" 行情連線模式設定 (Mode) ", padding=10)
        mode_frame.pack(fill="x", padx=5, pady=5)

        ttk.Label(mode_frame, text="行情模式 (Mode):").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.combo_mode = ttk.Combobox(
            mode_frame, 
            values=["Speed (極速) -> 僅支援 trades / books", "Normal (一般) -> 僅支援 candles / aggregates"], 
            width=48, 
            state="readonly"
        )
        self.combo_mode.current(0)
        self.combo_mode.grid(row=0, column=1, sticky="w", padx=5, pady=5)

        btn_connect_ws = ttk.Button(mode_frame, text="1. 啟動 WebSocket 連線", command=self.action_connect_ws)
        btn_connect_ws.grid(row=0, column=2, padx=10, pady=5)

        param_frame = ttk.LabelFrame(f, text=" 訂閱頻道與商品 ", padding=10)
        param_frame.pack(fill="x", padx=5, pady=5)

        ttk.Label(param_frame, text="市場別:").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.combo_market = ttk.Combobox(param_frame, values=["證券 (Stock)", "期貨 (FutOpt)"], width=15, state="readonly")
        self.combo_market.current(0)
        self.combo_market.grid(row=0, column=1, sticky="w", padx=5, pady=5)

        ttk.Label(param_frame, text="訂閱代碼 (Symbol):").grid(row=0, column=2, sticky="w", padx=5, pady=5)
        self.entry_ws_symbol = ttk.Entry(param_frame, width=15)
        self.entry_ws_symbol.insert(0, "2330")
        self.entry_ws_symbol.grid(row=0, column=3, sticky="w", padx=5, pady=5)

        ttk.Label(param_frame, text="訂閱頻道 (Channel):").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        self.combo_channel = ttk.Combobox(param_frame, values=["trades (逐筆成交)", "books (最佳五檔)"], width=22, state="readonly")
        self.combo_channel.current(0)
        self.combo_channel.grid(row=1, column=1, sticky="w", padx=5, pady=5)

        self.var_after_hours = tk.BooleanVar(value=self.is_after_hours())
        self.chk_after_hours = ttk.Checkbutton(param_frame, text="🌙 盤後夜盤行情 (afterHours)", variable=self.var_after_hours)
        self.chk_after_hours.grid(row=1, column=2, columnspan=2, sticky="w", padx=5, pady=5)

        btn_frame = ttk.Frame(f, padding=5)
        btn_frame.pack(fill="x", padx=5, pady=5)

        btn_sub = ttk.Button(btn_frame, text="2. 訂閱頻道", command=self.action_subscribe)
        btn_sub.pack(side="left", padx=5)

        btn_unsub = ttk.Button(btn_frame, text="3. 取消訂閱", command=self.action_unsubscribe)
        btn_unsub.pack(side="left", padx=5)

    # ------------------------------------------------------------------
    # 分頁 5：帳務與部位查詢 UI
    # ------------------------------------------------------------------
    def _build_tab_accounting(self):
        f = self.tab_accounting

        acc_frame = ttk.LabelFrame(f, text=" 選擇查詢帳號 ", padding=8)
        acc_frame.pack(fill="x", padx=5, pady=5)

        ttk.Label(acc_frame, text="切換帳號:").pack(side="left", padx=5)
        self.combo_acc_accounts = ttk.Combobox(acc_frame, width=45, state="readonly")
        self.combo_acc_accounts.pack(side="left", padx=5)

        ctrl_frame = ttk.LabelFrame(f, text=" 帳務查詢操作區 ", padding=10)
        ctrl_frame.pack(fill="x", padx=5, pady=5)

        btn_margin = ttk.Button(ctrl_frame, text="📊 查詢權益數 / 保證金", command=self.action_query_margin)
        btn_margin.pack(side="left", padx=5, pady=5)

        btn_position = ttk.Button(ctrl_frame, text="📦 查詢未平倉部位/持股", command=self.action_query_positions)
        btn_position.pack(side="left", padx=5, pady=5)

        btn_orders = ttk.Button(ctrl_frame, text="📜 查詢當日委託回報", command=self.action_query_orders)
        btn_orders.pack(side="left", padx=5, pady=5)

        btn_fills = ttk.Button(ctrl_frame, text="⚡ 查詢當日成交回報", command=self.action_query_fills)
        btn_fills.pack(side="left", padx=5, pady=5)

        self.lbl_acc_status = ttk.Label(f, text="提示: 請切換欲查詢之帳號後點擊對應按鈕。", foreground="#007acc")
        self.lbl_acc_status.pack(anchor="w", padx=5, pady=2)

        table_frame = ttk.Frame(f)
        table_frame.pack(fill="both", expand=True, padx=5, pady=5)

        self.acc_tree = ttk.Treeview(table_frame, show="headings", height=10)
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.acc_tree.yview)
        self.acc_tree.configure(yscroll=scrollbar.set)

        self.acc_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    # ------------------------------------------------------------------
    # 通用輔助與登入
    # ------------------------------------------------------------------
    def log(self, message, category="INFO"):
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        formatted_msg = f"[{now}] [{category}] {message}\n"
        self.root.after(0, self._append_log, formatted_msg)

    def _append_log(self, text):
        self.log_area.insert(tk.END, text)
        self.log_area.see(tk.END)

    def clear_log(self):
        self.log_area.delete("1.0", tk.END)

    def browse_cert_file(self):
        file_selected = filedialog.askopenfilename(
            title="選擇憑證檔案",
            filetypes=[("憑證檔案 (*.pfx;*.p12)", "*.pfx;*.p12"), ("所有檔案 (*.*)", "*.*")]
        )
        if file_selected:
            self.entry_cert_path.delete(0, tk.END)
            self.entry_cert_path.insert(0, file_selected)

    def action_login(self):
        threading.Thread(target=self._async_login, daemon=True).start()

    def _async_login(self):
        user_id = self.entry_id.get().strip()
        pwd = self.entry_pwd.get().strip()
        cert_path = self.entry_cert_path.get().strip()
        cert_pwd = self.entry_cert_pwd.get().strip()

        if not user_id or not pwd or not cert_path:
            self.log("請確實填寫身分證、密碼與憑證路徑！", "WARN")
            return

        self.log("正在進行 SDK 登入...")
        try:
            self.sdk = FubonSDK()
            result = self.sdk.login(user_id, pwd, cert_path, cert_pwd)
            self.log(f"Raw Login Result: {result}", "RAW")

            if result.is_success:
                self.log("✅ 登入成功！", "SUCCESS")
                self.accounts = result.data if isinstance(result.data, list) else [result.data]
                
                acc_options = []
                for a in self.accounts:
                    a_type = getattr(a, "account_type", "").lower()
                    type_label = "證券" if ("stock" in a_type or "證券" in a_type) else "期權"
                    acc_options.append(f"[{type_label}] 分公司: {a.branch_no} - 帳號: {a.account}")

                def _update_combos():
                    self.combo_accounts.config(values=acc_options)
                    self.combo_acc_accounts.config(values=acc_options)
                    if acc_options:
                        self.combo_accounts.current(0)
                        self.combo_acc_accounts.current(0)

                self.root.after(0, _update_combos)
            else:
                self.log(f"❌ 登入失敗: {result.message}", "ERROR")
        except Exception as e:
            self.log(f"登入過程發生例外異常: {str(e)}", "EXCEPT")

    def _get_active_account(self, from_accounting=False):
        if not self.sdk or not self.accounts:
            self.log("請先完成登入！", "WARN")
            return None
        combo = self.combo_acc_accounts if from_accounting else self.combo_accounts
        idx = combo.current()
        if idx < 0:
            self.log("請選擇操作帳號！", "WARN")
            return None
        return self.accounts[idx]

    # ------------------------------------------------------------------
    # 真實商品查詢
    # ------------------------------------------------------------------
    def parse_api_response_to_df(self, res):
        if res is None:
            return pd.DataFrame()

        data = None
        if hasattr(res, 'data'):
            data = getattr(res, 'data')
        elif isinstance(res, dict):
            data = res.get('data', res)
        else:
            data = res

        if data is None:
            return pd.DataFrame()

        if isinstance(data, list):
            clean_list = []
            for item in data:
                if hasattr(item, 'dict') and callable(getattr(item, 'dict')):
                    clean_list.append(item.dict())
                elif hasattr(item, '__dict__'):
                    clean_list.append(item.__dict__)
                elif isinstance(item, dict):
                    clean_list.append(item)
                else:
                    clean_list.append(item)
            return pd.DataFrame(clean_list)
        elif isinstance(data, dict):
            return pd.DataFrame([data])
            
        return pd.DataFrame()

    def action_search_symbol(self):
        if not self.sdk:
            self.log("請先完成 SDK 登入以啟用商品查詢功能！", "WARN")
            messagebox.showwarning("未登入", "請先在『1. 登入與帳號』分頁完成登入！")
            return

        threading.Thread(target=self._async_search_symbol, daemon=True).start()

    def _async_search_symbol(self):
        market_choice = self.combo_search_market.get()
        keyword = self.entry_search_keyword.get().strip()
        
        self.log(f"正在搜尋市場 [{market_choice}] 關鍵字: 『{keyword}』...")
        clean_kw = clean_taiwan_char(keyword).lower()

        try:
            if not hasattr(self.sdk, 'marketdata'):
                self.log("初始化行情 RestClient 模組中...", "SYS")
                self.sdk.init_realtime()

            rest_client = self.sdk.marketdata.rest_client

            if "期權" in market_choice:
                if self.cache_futopt_df is None or self.cache_futopt_df.empty:
                    self.log("首次發送 REST API 撈取期權全市場商品檔...", "SYS")
                    res_fut = rest_client.futopt.intraday.tickers(type='FUTURE', exchange='TAIFEX')
                    df_fut = self.parse_api_response_to_df(res_fut)

                    df_opt = pd.DataFrame()
                    try:
                        res_opt = rest_client.futopt.intraday.tickers(type='OPTION', exchange='TAIFEX')
                        df_opt = self.parse_api_response_to_df(res_opt)
                    except Exception:
                        pass

                    df_all = pd.concat([df_fut, df_opt], ignore_index=True)

                    if not df_all.empty:
                        rename_cols = {
                            "symbol": "商品完整代碼",
                            "name": "合約名稱",
                            "deliveryMonth": "到期年月",
                            "delivery_month": "到期年月",
                            "exchange": "交易所"
                        }
                        self.cache_futopt_df = df_all.rename(columns=rename_cols)
                    else:
                        self.cache_futopt_df = pd.DataFrame()

                df_source = self.cache_futopt_df
                if df_source.empty:
                    self.log("未撈取到任何期權商品檔！", "WARN")
                    return

                mask = pd.Series(False, index=df_source.index)
                for col in df_source.columns:
                    col_str = df_source[col].astype(str).apply(clean_taiwan_char).str.lower()
                    mask = mask | col_str.str.contains(clean_kw, na=False)

                filtered_df = df_source[mask].copy()
                self.root.after(0, lambda: self._update_symbol_tree_futopt(filtered_df, keyword))

            else:
                if self.cache_stocks_df is None or self.cache_stocks_df.empty:
                    self.log("首次發送 REST API 撈取證券全市場商品檔...", "SYS")
                    res_twse = rest_client.stock.intraday.tickers(type='EQUITY', exchange='TWSE')
                    df_twse = self.parse_api_response_to_df(res_twse)

                    df_tpex = pd.DataFrame()
                    try:
                        res_tpex = rest_client.stock.intraday.tickers(type='EQUITY', exchange='TPEx')
                        df_tpex = self.parse_api_response_to_df(res_tpex)
                    except Exception:
                        pass

                    df_stock = pd.concat([df_twse, df_tpex], ignore_index=True)

                    if not df_stock.empty:
                        rename_cols = {
                            "symbol": "股票代碼",
                            "name": "股票簡稱",
                            "market": "市場別",
                            "type": "商品類別"
                        }
                        self.cache_stocks_df = df_stock.rename(columns=rename_cols)
                    else:
                        self.cache_stocks_df = pd.DataFrame()

                df_source = self.cache_stocks_df
                if df_source.empty:
                    self.log("未撈取到任何證券商品檔！", "WARN")
                    return

                mask = pd.Series(False, index=df_source.index)
                for col in df_source.columns:
                    col_str = df_source[col].astype(str).apply(clean_taiwan_char).str.lower()
                    mask = mask | col_str.str.contains(clean_kw, na=False)

                filtered_df = df_source[mask].copy()
                self.root.after(0, lambda: self._update_symbol_tree_stock(filtered_df, keyword))

        except Exception as e:
            self.log(f"獲取商品清單發生例外: {str(e)}", "EXCEPT")

    def _update_symbol_tree_futopt(self, df, keyword):
        for item in self.symbol_tree.get_children():
            self.symbol_tree.delete(item)

        self._setup_tree_columns_for_futopt()
        self.current_search_results_df = df

        count = len(df)
        self.lbl_search_count.config(text=f"🔍 關鍵字『{keyword}』共找到 {count} 筆符合的期權商品")
        self.log(f"期權商品搜尋完成，共 {count} 筆。")

        for _, row in df.iterrows():
            sym = row.get("商品完整代碼") or row.get("symbol") or ""
            name = row.get("合約名稱") or row.get("name") or ""
            mth = row.get("到期年月") or row.get("deliveryMonth") or ""
            exc = row.get("交易所") or row.get("exchange") or ""
            self.symbol_tree.insert("", "end", values=(sym, name, mth, exc))

    def _update_symbol_tree_stock(self, df, keyword):
        for item in self.symbol_tree.get_children():
            self.symbol_tree.delete(item)

        self._setup_tree_columns_for_stock()
        self.current_search_results_df = df

        count = len(df)
        self.lbl_search_count.config(text=f"🔍 關鍵字『{keyword}』共找到 {count} 筆符合的證券商品")
        self.log(f"證券商品搜尋完成，共 {count} 筆。")

        for _, row in df.iterrows():
            sym = row.get("股票代碼") or row.get("symbol") or ""
            name = row.get("股票簡稱") or row.get("name") or ""
            mkt = row.get("市場別") or row.get("market") or ""
            tp = row.get("商品類別") or row.get("type") or ""
            self.symbol_tree.insert("", "end", values=(sym, name, mkt, tp))

    def copy_selected_symbol(self):
        selected = self.symbol_tree.selection()
        if not selected:
            messagebox.showinfo("提示", "請先點選清單中的商品列！")
            return

        item = self.symbol_tree.item(selected[0])
        symbol = item["values"][0]

        self.root.clipboard_clear()
        self.root.clipboard_append(str(symbol))
        self.log(f"📋 已複製商品代碼 [{symbol}] 至剪貼簿！", "SYS")
        messagebox.showinfo("成功", f"已複製代碼：{symbol}\n可直接貼至下單或行情頁面。")

    def action_export_csv(self):
        if self.current_search_results_df is None or self.current_search_results_df.empty:
            messagebox.showwarning("提示", "目前沒有可供匯出的搜尋結果！請先執行搜尋。")
            return

        file_path = filedialog.asksaveasfilename(
            title="匯出商品查詢結果為 CSV",
            defaultextension=".csv",
            filetypes=[("CSV 檔案 (*.csv)", "*.csv"), ("所有檔案 (*.*)", "*.*")],
            initialfile=f"fubon_symbols_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )

        if file_path:
            try:
                self.current_search_results_df.to_csv(file_path, index=False, encoding='utf-8-sig')
                self.log(f"📥 成功匯出 CSV 檔案至: {file_path}", "SUCCESS")
                messagebox.showinfo("匯出成功", f"商品資料已順利匯出至：\n{file_path}")
            except Exception as e:
                self.log(f"匯出 CSV 過程發生錯誤: {str(e)}", "EXCEPT")

    # ------------------------------------------------------------------
    # 自動判斷商品類別 (期貨 vs 選擇權) 與日夜盤 MarketType
    # ------------------------------------------------------------------
    def _detect_market_type(self, symbol):
        """依據商品代碼與當前時間，自動匹配富邦 FutOptMarketType"""
        sym_upper = symbol.strip().upper()
        after_hours = self.is_after_hours()

        # 選擇權常見代碼特徵 (TXO:台指選, TKO, CBO, 或是長度較長含履約價)
        is_option = sym_upper.startswith(("TXO", "TKO", "CAO", "CBO")) or len(sym_upper) >= 8

        if is_option:
            return FutOptMarketType.OptionNight if after_hours else FutOptMarketType.Option
        else:
            return FutOptMarketType.FutureNight if after_hours else FutOptMarketType.Future

    def _detect_condition_market_type(self, symbol):

        sym = symbol.upper()

        is_option = (
                sym.startswith(("TXO", "CAO", "CBO"))
                or len(sym) >= 8
            )

        if is_option:

                return (
                    FutOptConditionMarketType.OptionNight
                    if self.is_after_hours()
                    else FutOptConditionMarketType.Option
                )

        return (
                FutOptConditionMarketType.FutureNight
                if self.is_after_hours()
                else FutOptConditionMarketType.Future
            )

    # ------------------------------------------------------------------
    # 單式單下單 (完全符合富邦官方 sdk.stock.place_order 規格)
    # ------------------------------------------------------------------
    def action_send_single_order(self):
        threading.Thread(target=self._async_send_single_order, daemon=True).start()

    def _async_send_single_order(self):
        acc = self._get_active_account()
        if not acc: return

        symbol = self.entry_single_symbol.get().strip()
        price_str = self.entry_single_price.get().strip()
        qty = int(self.entry_single_qty.get().strip() or "1")
        acc_type = getattr(acc, "account_type", "").lower()
        is_stock_acc = "stock" in acc_type or "證券" in acc_type

        self.log(f"發送單式單 -> 帳號: {acc.account} ({'證券' if is_stock_acc else '期權'}), 商品: {symbol}, 價格: {price_str or '市價'}, 數量: {qty}")
        try:
            action = BSAction.Buy if "Buy" in self.combo_single_action.get() else BSAction.Sell
            tif = getattr(TimeInForce, self.combo_single_tif.get(), TimeInForce.ROD)
            is_market = "Market" in self.combo_single_price_type.get()

            # 🅰️ 證券下單通道 (2002 中鋼、2330 台積電等)
            if is_stock_acc:
                price_type = PriceType.Market if is_market else PriceType.Limit
                price_val = "" if is_market else str(price_str)

                # 對齊官方文件 Order 物件完整建構式
                stock_order = Order(
                    buy_sell=action,               # BSAction
                    symbol=symbol,                 # str
                    price=price_val,               # str
                    quantity=qty,                  # int
                    market_type=MarketType.Common, # 常規整股
                    price_type=price_type,         # PriceType.Limit / Market
                    time_in_force=tif,             # TimeInForce.ROD
                    order_type=OrderType.Stock     # 現股
                )
                
                res = self.sdk.stock.place_order(acc, stock_order)
                self.log(f"Raw Place Stock Order Response: {res}", "RAW")

            # 🅱️ 期權下單通道 (台指期、微台、選擇權)
            else:
                price_type = FutOptPriceType.Market if (is_market or price_str == "") else FutOptPriceType.Limit
                price_val = "" if is_market else str(price_str)
                market_type = self._detect_market_type(symbol)

                fut_order = FutOptOrder(
                    buy_sell=action,
                    symbol=symbol,
                    price=price_val,
                    lot=qty,
                    market_type=market_type,
                    price_type=price_type,
                    time_in_force=tif,
                    order_type=FutOptOrderType.Auto
                )
                res = self.sdk.futopt.place_order(acc, fut_order)
                self.log(f"Raw Place FutOpt Order Response: {res}", "RAW")

            if res and getattr(res, "is_success", False):
                order_no = getattr(getattr(res, "data", None), "order_no", res)
                self.log(f"✅ [單式單委託成功] 委託書號: {order_no}", "SUCCESS")
            else:
                msg = getattr(res, "message", res)
                self.log(f"❌ 委託被拒: {msg}", "ERROR")

        except Exception as e:
            self.log(f"單式單下單過程發生例外: {str(e)}", "EXCEPT")

    # ------------------------------------------------------------------
    # 跨月/選擇權複式單下單 (自動判別期權類型版)
    # ------------------------------------------------------------------
    def action_send_combo_order(self):
        threading.Thread(target=self._async_send_combo_order, daemon=True).start()

    def _async_send_combo_order(self):
        acc = self._get_active_account()
        if not acc: return

        leg1_sym = self.entry_leg1_symbol.get().strip()
        leg1_act = BSAction.Buy if "Buy" in self.combo_leg1_action.get() else BSAction.Sell
        
        leg2_sym = self.entry_leg2_symbol.get().strip()
        leg2_act = BSAction.Buy if "Buy" in self.combo_leg2_action.get() else BSAction.Sell
        
        price_spread = self.entry_combo_price.get().strip()
        qty = int(self.entry_combo_qty.get().strip() or "1")

        # 💡 自動識別腳位 1 的市場類別 (期貨 vs 選擇權 / 日盤 vs 夜盤)
        market_type = self._detect_market_type(leg1_sym)

        self.log(f"發送複式單 -> 帳號: {acc.account}, 腳1: {leg1_sym}, 腳2: {leg2_sym}, 價差/權利金差: {price_spread}, 市場類型: {market_type}")
        try:
            spread_order = FutOptOrder(
                buy_sell=leg1_act,        # 第一隻腳動作 (Buy/Sell)
                symbol=leg1_sym,
                buy_sell2=leg2_act,       # 第二隻腳動作 (Buy/Sell)
                symbol2=leg2_sym,
                price=str(price_spread),   # 價差或組合權利金
                lot=qty,
                market_type=market_type,   # 自動匹配 Future/Option/Night
                price_type=FutOptPriceType.Limit,
                time_in_force=TimeInForce.IOC,
                order_type=FutOptOrderType.Auto
            )

            res = self.sdk.futopt.place_order(acc, spread_order)
            self.log(f"Raw Place Combo Order Response: {res}", "RAW")

            if res and getattr(res, "is_success", False):
                order_no = getattr(getattr(res, "data", None), "order_no", res)
                self.log(f"✅ [複式單委託成功] 委託書號: {order_no}", "SUCCESS")
            else:
                msg = getattr(res, "message", res)
                self.log(f"❌ 複式單下單失敗: {msg}", "ERROR")

        except Exception as e:
            self.log(f"複式單下單過程發生例外: {str(e)}", "EXCEPT")

    # ------------------------------------------------------------------
    # 雲端條件單 (對齊官方 SingleCondition 與 4 大欄位 Condition)
    # ------------------------------------------------------------------
    def action_send_cond_order(self):
        threading.Thread(target=self._async_send_cond_order, daemon=True).start()

    def _async_send_cond_order(self):
        acc = self._get_active_account()
        if not acc: return

        cond_sym = self.entry_cond_symbol.get().strip()
        dir_str = self.combo_cond_direction.get()
        trig_price = self.entry_cond_trigger_price.get().strip()
        ord_act = BSAction.Buy if "Buy" in self.combo_cond_action.get() else BSAction.Sell
        ord_price = self.entry_cond_order_price.get().strip()
        qty = int(self.entry_cond_qty.get().strip() or "1")
        
        acc_type = getattr(acc, "account_type", "").lower()
        is_stock_acc = "stock" in acc_type or "證券" in acc_type

        start_date_input = (self.entry_cond_start_date.get().strip().replace("-", "").replace("/", ""))
        end_date_input = (self.entry_cond_end_date.get().strip().replace("-", "").replace("/", ""))

        if "GTE" in dir_str:
            trig_dir = Operator.GreaterThanOrEqual

        elif "GT" in dir_str:
            trig_dir = Operator.GreaterThan

        elif "LTE" in dir_str:
            trig_dir = Operator.LessThanOrEqual

        else:
            trig_dir = Operator.LessThan

        stop_sign = getattr(StopSign, 'Full', getattr(StopSign, 'FULL', 'Full'))
        is_market = ord_price == "" or "Market" in ord_price

        try:
            # 1. 建立符合官方規範的 Condition 物件
            cond_obj, cond_mode = create_fubon_condition(
                symbol=cond_sym,
                trigger_price=trig_price,
                trigger_dir=trig_dir,
                is_stock=is_stock_acc,
                is_after_hours=self.is_after_hours()
            )
            self.log(f"🔍 [Condition 建立成功] 採用模式: {cond_mode} | 物件: {cond_obj}", "DEBUG")

            # 2. 建立下單 Order 物件與指定 API 通道
            if is_stock_acc:
                order_obj = Order(
                    buy_sell=ord_act,
                    symbol=cond_sym,
                    price="" if is_market else str(ord_price),
                    quantity=qty,
                    market_type=MarketType.Common,
                    price_type=PriceType.Market if is_market else PriceType.Limit,
                    time_in_force=TimeInForce.ROD,
                    order_type=OrderType.Stock
                )
                target_module = getattr(self.sdk, 'stock', None)
            else:
                market_type = self._detect_condition_market_type(cond_sym)
                order_obj = FutOptConditionOrder(
                    buy_sell=ord_act,
                    symbol=cond_sym,
                    market_type=market_type,
                    price_type=(
                        FutOptConditionPriceType.Market
                        if is_market
                        else FutOptConditionPriceType.Limit
                    ),
                    time_in_force=TimeInForce.ROD,
                    order_type=FutOptConditionOrderType.New,
                    lot=qty,
                    price=None if is_market else str(ord_price)
                )

                target_module = getattr(self.sdk, 'futopt', None)

            self.log(f"發送[{'證券' if is_stock_acc else '期權'}]雲端條件單 -> 標的: {cond_sym}, 觸發價: {trig_price}, 下單價: {ord_price or '市價'}...")
            
            # 3. 呼叫 single_condition
            self.log(f"start_date={start_date_input}")
            self.log(f"end_date={end_date_input}")
            res = target_module.single_condition(
                account=acc,
                start_date=start_date_input,
                end_date=end_date_input,
                stop_sign=stop_sign,
                condition=cond_obj,
                order=order_obj
            )

            self.log(f"Raw Condition Order Response: {res}", "RAW")

            if res and getattr(res, "is_success", False):
                self.log("✅ [雲端條件單建立成功]", "SUCCESS")
            else:
                msg = getattr(res, "message", res) if res else "發送失敗"
                self.log(f"❌ 條件單建立失敗: {msg}", "ERROR")

        except Exception as e:
            self.log(f"雲端條件單發送過程發生例外: {str(e)}", "EXCEPT")

    # ------------------------------------------------------------------
    # 💡 修正 1：行情 WebSocket (含 Smart Fallback 退訂尋找)
    # ------------------------------------------------------------------
    def action_connect_ws(self):
        threading.Thread(target=self._async_connect_ws, daemon=True).start()

    def _async_connect_ws(self):
        if not self.sdk:
            self.log("請先完成 SDK 登入！", "WARN")
            return

        chosen_mode_str = self.combo_mode.get()
        target_mode = Mode.Speed if "Speed" in chosen_mode_str else Mode.Normal

        self.log(f"建立行情 WebSocket 監聽連線 (模式: {target_mode})...")
        try:
            self.sdk.init_realtime(target_mode)
            self.current_mode = target_mode

            market_type = self.combo_market.get()
            self.ws = self.sdk.marketdata.websocket_client.stock if "證券" in market_type else self.sdk.marketdata.websocket_client.futopt

            def handle_message(message):
                self.log(f"[WS 接收行情] {message}", "WS_MSG")
                try:
                    data = json.loads(message) if isinstance(message, str) else message
                    event = data.get("event")
                    event_data = data.get("data", {})

                    if event == "subscribed":
                        sub_id = event_data.get("id")
                        symbol = event_data.get("symbol")
                        channel = event_data.get("channel")
                        after_hours = bool(event_data.get("afterHours", False))

                        if sub_id and symbol and channel:
                            key = f"{symbol}_{channel}_{after_hours}"
                            self.sub_key_map[key] = sub_id
                            self.sub_id_map[sub_id] = key
                            self.log(f"📌 訂閱成功！已紀錄 Key [{key}] -> ID [{sub_id}]", "SYS")

                    elif event == "unsubscribed":
                        unsub_id = event_data.get("id")
                        if unsub_id and unsub_id in self.sub_id_map:
                            key = self.sub_id_map.pop(unsub_id)
                            self.sub_key_map.pop(key, None)
                            self.log(f"🗑️ 已移除退訂成功的頻道紀錄 Key [{key}]", "SYS")
                except Exception:
                    pass

            def handle_connect():
                self.log(f"✅ WebSocket 行情伺服器已連線 ({target_mode} 模式)", "WS_EVENT")

            def handle_disconnect(code, message):
                self.log(f"⚠️ WebSocket 斷線: [{code}] {message}", "WS_EVENT")

            def handle_error(error):
                self.log(f"❌ WebSocket 錯誤: {error}", "WS_ERROR")

            self.ws.on('message', handle_message)
            self.ws.on('connect', handle_connect)
            self.ws.on('disconnect', handle_disconnect)
            self.ws.on('error', handle_error)

            self.ws.connect()
            self.log("WebSocket connect 命令已發送，等待連線回應...")

        except Exception as e:
            self.log(f"建立 WebSocket 時發生例外: {str(e)}", "EXCEPT")

    def action_subscribe(self):
        symbol = self.entry_ws_symbol.get().strip()
        channel_choice = self.combo_channel.get().split()[0]
        use_after_hours = self.var_after_hours.get()

        if not self.ws:
            self.log("請先點擊「1. 啟動 WebSocket 連線」！", "WARN")
            return

        try:
            sub_payload = {
                "channel": channel_choice,
                "symbol": symbol,
                "afterHours": use_after_hours
            }
            self.log(f"發送行情訂閱請求: {sub_payload}")
            self.ws.subscribe(sub_payload)
        except Exception as e:
            self.log(f"訂閱失敗: {str(e)}", "EXCEPT")

    def action_unsubscribe(self):
        symbol = self.entry_ws_symbol.get().strip()
        channel_choice = self.combo_channel.get().split()[0]
        use_after_hours = self.var_after_hours.get()

        if not self.ws:
            return

        # 精確對應 Key
        key = f"{symbol}_{channel_choice}_{use_after_hours}"
        sub_id = self.sub_key_map.get(key)

        # 💡 Smart Fallback：若因證券缺 afterHours 欄位導致精確 Key 查無結果，進行字串模糊匹配
        if not sub_id:
            prefix = f"{symbol}_{channel_choice}"
            for k, v in self.sub_key_map.items():
                if k.startswith(prefix):
                    sub_id = v
                    break

        try:
            if sub_id:
                self.log(f"發送取消行情訂閱請求 (帶 ID): {symbol} (ID: {sub_id})")
                self.ws.unsubscribe({"id": sub_id})
            else:
                self.log(f"發送取消行情訂閱請求 (未帶 ID): {symbol}")
                self.ws.unsubscribe({
                    "channel": channel_choice,
                    "symbol": symbol,
                    "afterHours": use_after_hours
                })
        except Exception as e:
            self.log(f"取消訂閱失敗: {str(e)}", "EXCEPT")

    # ------------------------------------------------------------------
    # 分頁 5：帳務與部位查詢邏輯
    # ------------------------------------------------------------------
    def _safe_call_sdk_api(self, acc, candidate_configs):
        for module, method_names in candidate_configs:
            if not module:
                continue
            for name in method_names:
                if hasattr(module, name):
                    func = getattr(module, name)
                    if callable(func):
                        self.log(f"✅ 找到對應 API 方法: {module.__class__.__name__}.{name}()", "SYS")
                        return func(acc)

        for module, _ in candidate_configs:
            if module:
                mod_name = module.__class__.__name__
                methods = [m for m in dir(module) if not m.startswith('_')]
                self.log(f"🔍 [SDK 診斷] {mod_name} 可用方法: {methods}", "DEBUG")

        raise AttributeError("找不到對應的 API 函數，請對照上方的 SDK 診斷 Log。")

    def action_query_margin(self):
        threading.Thread(target=self._async_query_margin, daemon=True).start()

    def _async_query_margin(self):
        acc = self._get_active_account(from_accounting=True)
        if not acc: return

        a_type = getattr(acc, "account_type", "").lower()
        is_stock = "stock" in a_type or "證券" in a_type
        self.log(f"查詢權益數與保證金報告 -> 帳號: {acc.account} (類別: {'證券' if is_stock else '期權'})...")

        try:
            if not is_stock:
                res = self.sdk.futopt_accounting.query_margin_equity(acc)
            else:
                res = self.sdk.accounting.unrealized_gains_and_loses(acc)

            self.log(f"Raw Accounting Response: {res}", "RAW")

            if hasattr(res, "is_success") and not res.is_success:
                err_msg = getattr(res, "message", "未知錯誤")
                self.log(f"❌ 查詢帳務失敗: {err_msg}", "ERROR")
                self.root.after(0, lambda: self.lbl_acc_status.config(text=f"❌ 查詢失敗: {err_msg}"))
                return

            clean_data = to_clean_list(res)
            actual_list = clean_data[0]["data"] if clean_data and "data" in clean_data[0] and isinstance(clean_data[0]["data"], list) else clean_data

            df = pd.DataFrame(actual_list)
            self.root.after(0, lambda: self._update_accounting_tree(df, f"📊 [{'證券' if is_stock else '期權'}] 帳務/權益查詢結果"))

        except Exception as e:
            self.log(f"查詢帳務權益過程發生例外: {str(e)}", "EXCEPT")

    def action_query_positions(self):
        threading.Thread(target=self._async_query_positions, daemon=True).start()

    def _async_query_positions(self):
        acc = self._get_active_account(from_accounting=True)
        if not acc: return

        a_type = getattr(acc, "account_type", "").lower()
        is_stock = "stock" in a_type or "證券" in a_type
        self.log(f"查詢當前未平倉部位/持股 -> 帳號: {acc.account} (類別: {'證券' if is_stock else '期權'})...")

        try:
            if not is_stock:
                configs = [
                    (getattr(self.sdk, 'futopt_accounting', None), ['query_single_position', 'query_hybrid_position']),
                    (getattr(self.sdk, 'futopt', None), ['query_single_position', 'query_hybrid_position'])
                ]
            else:
                configs = [
                    (getattr(self.sdk, 'accounting', None), ['unrealized_gains_and_loses']),
                    (getattr(self.sdk, 'stock', None), ['get_positions'])
                ]

            res = self._safe_call_sdk_api(acc, configs)
            self.log(f"Raw Positions Response: {res}", "RAW")

            if hasattr(res, "is_success") and not res.is_success:
                err_msg = getattr(res, "message", "未知錯誤")
                self.log(f"❌ 查詢部位失敗: {err_msg}", "ERROR")
                self.root.after(0, lambda: self.lbl_acc_status.config(text=f"❌ 查詢部位失敗: {err_msg}"))
                return

            clean_data = to_clean_list(res)
            actual_list = clean_data[0]["data"] if clean_data and "data" in clean_data[0] and isinstance(clean_data[0]["data"], list) else clean_data

            df = pd.DataFrame(actual_list)
            self.root.after(0, lambda: self._update_accounting_tree(df, f"📦 [{'證券' if is_stock else '期權'}] 當前部位/庫存持股"))

        except Exception as e:
            self.log(f"查詢部位過程發生例外: {str(e)}", "EXCEPT")

    def action_query_orders(self):
        threading.Thread(target=self._async_query_orders, daemon=True).start()

    def _async_query_orders(self):
        acc = self._get_active_account(from_accounting=True)
        if not acc: return

        a_type = getattr(acc, "account_type", "").lower()
        is_stock = "stock" in a_type or "證券" in a_type
        self.log(f"查詢當日委託回報 -> 帳號: {acc.account}...")

        try:
            if not is_stock:
                res = self.sdk.futopt.get_order_results(acc)
            else:
                res = self.sdk.stock.get_order_results(acc) if hasattr(self.sdk, 'stock') else self.sdk.futopt.get_order_results(acc)

            self.log(f"Raw Order Results Response: {res}", "RAW")

            if hasattr(res, "is_success") and not res.is_success:
                err_msg = getattr(res, "message", "未知錯誤")
                self.log(f"❌ 查詢委託失敗: {err_msg}", "ERROR")
                self.root.after(0, lambda: self.lbl_acc_status.config(text=f"❌ 查詢委託失敗: {err_msg}"))
                return

            clean_data = to_clean_list(res)
            actual_list = clean_data[0]["data"] if clean_data and "data" in clean_data[0] and isinstance(clean_data[0]["data"], list) else clean_data

            df = pd.DataFrame(actual_list)
            self.root.after(0, lambda: self._update_accounting_tree(df, f"📜 [{'證券' if is_stock else '期權'}] 當日委託回報明細"))

        except Exception as e:
            self.log(f"查詢委託回報過程發生例外: {str(e)}", "EXCEPT")

    # ------------------------------------------------------------------
    # 當日成交回報 (全市場類別自動全檢與資料合併版)
    # ------------------------------------------------------------------
    def action_query_fills(self):
        threading.Thread(target=self._async_query_fills, daemon=True).start()

    def _async_query_fills(self):
        acc = self._get_active_account(from_accounting=True)
        if not acc: return

        a_type = getattr(acc, "account_type", "").lower()
        is_stock = "stock" in a_type or "證券" in a_type
        
        today_dash = datetime.date.today().strftime('%Y-%m-%d')
        today_nodash = datetime.date.today().strftime('%Y%m%d')

        self.log(f"查詢當日成交回報 -> 帳號: {acc.account} ({'證券' if is_stock else '期權'})...")

        try:
            combined_fills = []

            # 🅰️ 證券成交回報 / 已實現損益
            if is_stock:
                if hasattr(self.sdk, 'accounting') and hasattr(self.sdk.accounting, 'realized_gains_and_loses'):
                    try:
                        res = self.sdk.accounting.realized_gains_and_loses(acc)
                        clean = to_clean_list(res)
                        data = clean[0]["data"] if clean and isinstance(clean[0], dict) and "data" in clean[0] and isinstance(clean[0]["data"], list) else clean
                        if isinstance(data, list) and len(data) > 0:
                            combined_fills.extend(data)
                    except Exception as e_acc:
                        self.log(f"調用 accounting.realized_gains_and_loses 提示: {e_acc}", "DEBUG")

                if not combined_fills:
                    for d_fmt in [today_dash, today_nodash]:
                        try:
                            res = self.sdk.stock.filled_history(acc, d_fmt, d_fmt)
                            clean = to_clean_list(res)
                            data = clean[0]["data"] if clean and isinstance(clean[0], dict) and "data" in clean[0] and isinstance(clean[0]["data"], list) else clean
                            if isinstance(data, list) and len(data) > 0:
                                combined_fills.extend(data)
                                break
                        except Exception:
                            pass

            # 🅱️ 期權成交回報 (全盤別/全商品類別自動輪詢合併: 日期、夜期、日選、夜選)
            else:
                market_types = [
                    getattr(FutOptMarketType, 'Future', 'Future'),
                    getattr(FutOptMarketType, 'Option', 'Option'),
                    getattr(FutOptMarketType, 'FutureNight', 'FutureNight'),
                    getattr(FutOptMarketType, 'OptionNight', 'OptionNight')
                ]
                
                for m_type in market_types:
                    for d_fmt in [today_dash, today_nodash]:
                        try:
                            res = self.sdk.futopt.filled_history(acc, m_type, d_fmt, d_fmt)
                            if res and getattr(res, "is_success", False):
                                clean = to_clean_list(res)
                                data = clean[0]["data"] if clean and isinstance(clean[0], dict) and "data" in clean[0] and isinstance(clean[0]["data"], list) else clean
                                if isinstance(data, list) and len(data) > 0:
                                    combined_fills.extend(data)
                                    break # 該 market_type 查到當天資料後跳出日期格式迴圈
                        except Exception as e_fut:
                            self.log(f"輪詢盤別 [{m_type}] 日期 [{d_fmt}] 提示: {e_fut}", "DEBUG")

            # 轉為 DataFrame 並繪製至 Treeview 表格
            df = pd.DataFrame(combined_fills)
            self.root.after(0, lambda: self._update_accounting_tree(df, f"⚡ [{'證券' if is_stock else '期權'}] 當日成交回報明細"))

        except Exception as e:
            self.log(f"查詢成交回報過程發生例外: {str(e)}", "EXCEPT")

    def _update_accounting_tree(self, df, title):
        for item in self.acc_tree.get_children():
            self.acc_tree.delete(item)

        if df.empty:
            self.lbl_acc_status.config(text=f"{title}: 查無資料或尚無數據。")
            self.log(f"{title}: 查無資料或回傳空表格。")
            return

        cols = list(df.columns)
        self.acc_tree["columns"] = cols

        for col in cols:
            self.acc_tree.heading(col, text=col)
            self.acc_tree.column(col, width=130, anchor="center")

        for _, row in df.iterrows():
            vals = [str(row[c]) for c in cols]
            self.acc_tree.insert("", "end", values=vals)

        count = len(df)
        self.lbl_acc_status.config(text=f"✅ {title} 查詢成功！共 {count} 筆紀錄。")
        self.log(f"{title} 成功渲染至表格，共 {count} 筆。")

if __name__ == "__main__":
    root = tk.Tk()
    app = FullFeaturedAPITesterApp(root)
    
    def on_closing():
        root.destroy()
        os._exit(0)

    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()

    