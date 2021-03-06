import copy
import datetime
import random
import logging
import simplejson as json 
import iso8601

# Find code directory relative to our directory
from os.path import dirname, abspath, join
import sys
THIS_DIR = dirname(__file__)
CODE_DIR = abspath(join(THIS_DIR, '..', '..' ))
sys.path.append(CODE_DIR)
#from market_maker.settings import settings
#from market_maker import market_maker
from market_maker.utils import constants
#log orders to file




class PaperTrading:

    def __init__(self, settings=None,  logger="orders"):
        self.settings = settings
        self.buy_orders_created = []
        self.sell_orders_created = []
        self.filled = []
        self.buy_partially_filled = []
        self.sell_partially_filled = []
        self.closed = []
        self.seen_trades = set()
        self.random_base = random.randint(0, 100000)
        #self.exchange = market_maker.ExchangeInterface(self.settings.DRY_RUN)
        self.timestamp = None
        self.auxFunds = 0
        self.position = self.position = {'avgCostPrice': 0, 'avgEntryPrice': 0, 'currentQty': 0, 'symbol': "XBTUSD"}
        self.symbol = self.settings.SYMBOL
        self.pt_logger = logging.getLogger(logger)
        self.pt_logger.setLevel(logging.INFO)

    def reset(self):
        self.buy_orders_created = []
        self.sell_orders_created = []
        self.filled = []
        self.buy_partially_filled = []
        self.sell_partially_filled = []
        self.closed = []
        self.seen_trades = set()
        self.random_base = random.randint(0, 100000)
        #self.exchange = market_maker.ExchangeInterface(self.settings.DRY_RUN)
        self.timestamp = None
        self.auxFunds = 0
        self.position = self.position = {'avgCostPrice': 0, 'avgEntryPrice': 0, 'currentQty': 0, 'symbol': "XBTUSD"}
        self.symbol = self.settings.SYMBOL

    def provide_exchange(self, exchange):
        self.exchange = exchange

    def exchange_cancels_order(self, order):
        order_out = {
            'status' : 'Cancelled',
            'PAPERTRADING' : self.settings.PAPERTRADING,
            'type' : 'Paper',
            'agress' : False,
            'text': 'Cancelled for crossing book.',
            'data' : order
        }
        self.pt_logger.info(json.dumps(order_out))



    def track_orders_created(self, order):
        buy_orders = []
        sell_orders = []
        for orders in order:
            orders['timestamp'] = self.exchange.current_timestamp().isoformat()
            order_out = {
            'status': 'Created',
            'PAPERTRADING' : self.settings.PAPERTRADING,
            'type' : 'Paper',
            'data' : orders
            }
            self.pt_logger.info(json.dumps(order_out))
            if orders["side"] == "Buy":
                buy_orders.append(copy.deepcopy(orders))
            else:
                sell_orders.append(copy.deepcopy(orders))

        if len(buy_orders) > 0:
            self.buy_orders_created.extend(buy_orders)
        if len(sell_orders) > 0:
            self.sell_orders_created.extend(sell_orders)

        order_book = self.exchange.market_depth(self.symbol)
        ask = []
        bid = []
        order_table = {} 
        best_bid = 0
        best_ask = 10000000
        for orders in order_book:
            order_table[orders['price']] = orders
            if orders['side'] == 'Sell' and orders['price'] < best_ask:
                best_ask = orders['price']
            if orders['side'] == 'Buy' and orders['price'] > best_bid:
                best_bid = orders['price']
            if orders['side'] == "Sell":
                ask.append(orders)
            else:
                bid.append(orders)

        trades = self.exchange.recent_trades()
        if len(trades) > 0:
            last_trade = trades[-1]
        else:
            last_trade = {}
        orderbook_out = {
            'status': 'Orderbook',
            'PAPERTRADING' : self.settings.PAPERTRADING,
            'type' : 'Paper',
            'last_trade' : last_trade,
            'data' : order_book
        }
        self.pt_logger.info(json.dumps(orderbook_out, default=lambda o: '<not serializable>'))

        # Determine Price Remapping 
        # Price re-mapping is used to trigger cancellation of prices if the 
        # prices have moved since the orderbook was retrieved
        # Price re-mapping does not currently change the queue for the order
        order_book_time = self.exchange.get_orderbook_time()
        
        if len(trades) > 0:
            last_trade = trades[-1]
            trade_time = iso8601.parse_date(last_trade['timestamp'])
            ob_price_remap = None
            if trade_time > order_book_time:
                if last_trade['side'].lower() == 'buy' and \
                    last_trade['price'] > best_ask:
                    ob_price_remap =  last_trade['price'] - best_ask
                if last_trade['side'].lower() == 'sell' and \
                    last_trade['price'] < best_bid:
                    ob_price_remap =  last_trade['price'] - best_bid
            else:
                ob_price_remap = 0
        else:
            ob_price_remap = 0

        if ob_price_remap is None:
            raise Exception("PaperTrading.track_orders_created - There may be an issue with the orderbook")
         
        # let's not do market orders during backtests
        # send all orders to partially filled list
        default_level = {'size':0}
        if self.settings.BACKTEST:
            for buy_order in buy_orders:
                self.random_base += 1
                buy_order["cumQty"] = 0
                buy_order["leavesQty"] = buy_order["orderQty"] - buy_order["cumQty"]
                buy_order['amount_at_level'] = order_table.get(buy_order['price'],default_level)['size'] 
                buy_order['remaining_at_level'] = order_table.get(buy_order['price'],default_level)['size'] 
                #remapped_price = buy_order['price'] - ob_price_remap
                highest_allowable_buy =  best_bid + ob_price_remap
                #if buy_order['price'] in order_table and \
                #order_table[remapped_price]['side'] == 'Sell':
                if buy_order['price'] > highest_allowable_buy:
                    self.exchange_cancels_order(buy_order)
                else:
                    self.buy_partially_filled.append(buy_order)

            for sell_order in sell_orders:
                self.random_base += 1
                sell_order["cumQty"] = 0
                sell_order["leavesQty"] = sell_order["orderQty"] - sell_order["cumQty"]
                sell_order['amount_at_level'] = order_table.get(sell_order['price'],default_level)['size'] 
                sell_order['remaining_at_level'] = order_table.get(sell_order['price'],default_level)['size'] 
                #remapped_price = sell_order['price'] - ob_price_remap
                lowest_allowable_sell = best_ask + ob_price_remap
                #if sell_order['price'] in order_table and \
                #    order_table[remapped_price]['side'] == 'Buy':
                if sell_order['price'] < lowest_allowable_sell:
                    self.exchange_cancels_order(sell_order)
                else:
                    self.sell_partially_filled.append(sell_order)

            return

        # Market Orders Code - CURRENTLY UNUSED!!!!
        for orders in buy_orders:
            self.random_base += 1
            #orders["orderID"] = self.random_base
            orders["cumQty"] = 0
            orders["leavesQty"] = orders["orderQty"] - orders["cumQty"]
            orders['amount_at_level'] = order_table.get(orders['price'],default_level)['size'] 
            orders['remaining_at_level'] = order_table.get(orders['price'],default_level)['size'] 
            for i in range(len(ask) - 1, -1, -1):
                temp = ask[i]
                if orders["price"] >= temp["price"] and temp["size"] > 0:
                    if (orders["orderQty"] - orders["cumQty"]) >= temp["size"]:
                        orders["cumQty"] = orders["cumQty"] + temp["size"]
                        self.calculate_position(orders, temp["size"])
                        orders["leavesQty"] = orders["orderQty"] - orders["cumQty"]
                        #self.insert_to_log("Order Partially Filled - ID:" + str(orders["orderID"]) + " " + orders["side"] + " " + str(orders["cumQty"]) + " @ " + str(orders["price"]) + " " + " Total size: " + str(orders["orderQty"]) + " By OderBook: " + str(temp["size"]) + " @ " + str(temp["price"]))
                        orders['timestamp'] = self.exchange.current_timestamp().isoformat()
                        order_out = {
                            'status' : 'Partially Filled',
                            'PAPERTRADING' : self.settings.PAPERTRADING,
                            'type' : 'Paper',
                            'agress' : True,
                            'fillprice' : temp["price"],
                            'fillsize' : temp["size"], 
                            'data' : orders
                        }
                        self.pt_logger.info(json.dumps(order_out))
                        temp["size"] = 0
                    else:
                        temp["size"] = temp["size"] - (orders["orderQty"] - orders["cumQty"])
                        self.calculate_position(orders, (orders["orderQty"] - orders["cumQty"]))
                        orders["cumQty"] = orders["orderQty"]
                        orders["leavesQty"] = 0
                        self.filled.append(orders)
                        #self.insert_to_log("Order Filled - ID:" + str(orders["orderID"]) + " " + orders["side"] + " " + str(orders["orderQty"]) + " @ " + str(orders["price"]) + " By OderBook: " + str(temp["size"]) + " @ " + str(temp["price"]))
                        orders['timestamp'] = self.exchange.current_timestamp().isoformat()
                        order_out = {
                            'status' : 'Filled',
                            'PAPERTRADING' : self.settings.PAPERTRADING,
                            'type' : 'Paper',
                            'agress' : True,
                            'fillprice' : temp["price"],
                            'fillsize' : orders["orderQty"] - orders["cumQty"], 
                            'data' : orders
                        }
                        self.pt_logger.info(json.dumps(order_out))
                        break
            else:
                default_level = {'size':0}
                orders['amount_at_level'] = order_table.get(orders['price'],default_level)['size'] 
                orders['remaining_at_level'] = order_table.get(orders['price'],default_level)['size'] 
                self.buy_partially_filled.append(orders)
                #self.insert_to_log("Order Created - ID:" + str(orders["orderID"]) + " " + orders["side"] + " " + str(orders["cumQty"]) + " @ " + str(orders["price"]) + " " + " Total size: " + str(orders["orderQty"]))

        for orders in sell_orders:
            self.random_base += 1
            #orders["orderID"] = self.random_base
            orders["cumQty"] = 0
            orders["leavesQty"] = orders["orderQty"] - orders["cumQty"]
            orders['amount_at_level'] = order_table.get(orders['price'],default_level)['size'] 
            orders['remaining_at_level'] = order_table.get(orders['price'],default_level)['size'] 
            for i in range(0, len(bid)):
                temp = bid[i]
                if orders["price"] <= temp["price"] and temp["size"] > 0:
                    if (orders["orderQty"] - orders["cumQty"]) >= temp["size"]:
                        orders["orderQty"] = orders["orderQty"]
                        orders["cumQty"] = orders["cumQty"] + temp["size"]
                        self.calculate_position(orders, temp["size"])
                        orders["leavesQty"] = orders["orderQty"] - orders["cumQty"]
                        #self.insert_to_log("Order Partially Filled - ID:" + str(orders["orderID"]) + " " + orders["side"] + " " + str(orders["orderQty"]) + " @ " + str(orders["price"]) + " " + " Total size: " + str(orders["orderQty"]) + " By OderBook: " + str(temp["size"]) + " @ " + str(temp["price"]))  
                        orders['timestamp'] = self.exchange.current_timestamp().isoformat()
                        order_out = {
                            'status' : 'Partially Filled',
                            'PAPERTRADING' : self.settings.PAPERTRADING,
                            'type' : 'Paper',
                            'agress' : True,
                            'fillprice' : temp["price"],
                            'fillsize' : temp["size"], 
                            'data' : orders
                        }
                        self.pt_logger.info(json.dumps(order_out))
                        temp["size"] = 0
                    else:
                        temp["size"] = temp["size"] - (orders["orderQty"] - orders["cumQty"])
                        self.calculate_position(orders, (orders["orderQty"] - orders["cumQty"]))
                        orders["cumQty"] = orders["orderQty"]
                        orders["leavesQty"] = 0
                        self.filled.append(orders)
                        #self.insert_to_log("Order Filled - ID:" + str(orders["orderID"]) + " " + orders["side"] + " " + str(orders["orderQty"]) + " @ " + str(orders["price"]) + " By OderBook: " + str(temp["size"]) + " @ " + str(temp["price"]))
                        orders['timestamp'] = self.exchange.current_timestamp().isoformat()
                        order_out = {
                            'status' : 'Filled',
                            'PAPERTRADING' : self.settings.PAPERTRADING,
                            'type' : 'Paper',
                            'agress' : True,
                            'fillprice' : temp["price"],
                            'fillsize' : orders["orderQty"] - orders["cumQty"], 
                            'data' : orders
                        }
                        self.pt_logger.info(json.dumps(order_out))
                        break
            else:
                default_level = {'size':0}
                orders['amount_at_level'] = order_table.get(orders['price'],default_level)['size'] 
                orders['remaining_at_level'] = order_table.get(orders['price'],default_level)['size'] 
                self.sell_partially_filled.append(orders)
                #self.insert_to_log("Order Created - ID:" + str(orders["orderID"]) + " " + orders["side"] + " " + str(orders["cumQty"]) + " @ " + str(orders["price"]) + " " + " Total size: " + str(orders["orderQty"]))
    
    def _fill_orders_queued(self, all_orders, filtered_trades):
        for order in all_orders:
            orignal_size = order["orderQty"]
            #don't fill more than once
            if orignal_size - order["cumQty"] == 0:
                continue
            order_time = iso8601.parse_date(order['timestamp'])
            for temp in filtered_trades:
                temp_time = iso8601.parse_date(temp["timestamp"])
                if temp_time < order_time:
                    continue
                at_match_price =  order["price"] >= temp["price"] \
                                    if order['side'] is 'Buy' else \
                                    order["price"] <= temp["price"]
                if at_match_price and temp["size"] > 0:
                    #comment out the following if statement to remove queued ordering
                    past_match_price =  order["price"] > temp["price"] \
                                        if order['side'] is 'Buy' else \
                                         order["price"] < temp["price"]
                    # if real data has moved past our level, assume queue is empty
                    # this assumption works best if we have small orders
                    if not past_match_price:
                        if order['remaining_at_level'] - temp["size"] >= 0:
                            order['remaining_at_level'] = order['remaining_at_level'] - temp["size"]
                            continue
                        #only fill what isn't needed to clear the queue ahead of our order
                        temp["size"] = temp["size"] - order['remaining_at_level']
                    new_temp_timestamp =  iso8601.parse_date(temp["timestamp"]).isoformat()
                    if (orignal_size - order["cumQty"]) >= temp["size"]:
                        #only partially fill order
                        order["orderQty"] = orignal_size
                        filled_size = temp["size"]
                        order["cumQty"] = order["cumQty"] + temp["size"]
                        self.calculate_position(order, temp["size"])
                        order["leavesQty"] = order["orderQty"] - order["cumQty"]
                        self.timestamp = temp["timestamp"]
                        order['timestamp'] = self.exchange.current_timestamp().isoformat()
                        order['remaining_at_level'] = 0
                        order_out = {
                        'status' : 'Partially Filled',
                        'PAPERTRADING' : self.settings.PAPERTRADING,
                        'type' : 'Paper',
                        'agress' : False,
                        'fillprice' : temp["price"],
                        'fillsize' : filled_size, 
                        'match_order_timestamp': new_temp_timestamp,
                        'data' : order
                        }
                        self.pt_logger.info(json.dumps(order_out))
                        temp["size"] = 0
                    else:
                        #fully fill order
                        temp["size"] = temp["size"] - (orignal_size - order["cumQty"])
                        filled_size = orignal_size - order["cumQty"]
                        self.calculate_position(order, (order["orderQty"] - order["cumQty"]))
                        order["orderQty"] = orignal_size
                        order["cumQty"] = orignal_size
                        order["leavesQty"] = 0
                        self.timestamp = temp["timestamp"]
                        order['timestamp'] = self.exchange.current_timestamp().isoformat()
                        order_out = {
                        'status' : 'Filled',
                        'PAPERTRADING' : self.settings.PAPERTRADING,
                        'type' : 'Paper',
                        'agress' : False,
                        'fillprice' : temp["price"],
                        'fillsize' : filled_size, 
                        'match_order_timestamp': new_temp_timestamp,
                        'data' : order
                        }
                        self.pt_logger.info(json.dumps(order_out))
                        break
    
    
    def simulate_fills_from_trades(self):
        '''Tracks fills based on market trades. (Replaces track_orders.)'''

        trades = self.exchange.recent_trades()
        #self.timestamp = self.exchange.current_timestamp()
        #get only new trades to check
        new_trades = []
        sell = []
        buy = []
        filtered_trades = []
        for trade in trades:
            #trade_date = iso8601.parse_date(trade["timestamp"])
            #if self.timestamp == None or trade_date >= self.timestamp:

            tradeID = trade["trdMatchID"]
            if not (tradeID in self.seen_trades):
                filtered_trades.append(trade)
                if self.settings.LOG_TRADES_TO_ORDERS:
                    new_keys = ['timestamp','trdMatchID', 'price', 'size', 'side']
                    _trade = { key: trade[key] for key in new_keys}
                    trade_out = {
                            'status' : 'Traded',
                            'data' : _trade
                    }
                    self.pt_logger.info(json.dumps(trade_out))

                #if trade['side'] == "Sell":
                #    sell.append(trade)
                #else:
                #    buy.append(trade)
            self.seen_trades.add(tradeID)
        if len(filtered_trades)==0:
            return
        # Note that filling buy orders and sell orders seperately can cause fill prints to be 
        # printed non-chronologically 
        self._fill_orders_queued(self.buy_partially_filled, filtered_trades)
        self._fill_orders_queued(self.sell_partially_filled, filtered_trades)
        #self._fill_orders(self.buy_partially_filled, filtered_trades)
        #self._fill_orders(self.sell_partially_filled, filtered_trades)
        
        #use the timestamp from the last trade
        self.timestamp = iso8601.parse_date(filtered_trades[-1]['timestamp'])

        #Fill any orders that are completely filled
        self.from_partially_to_filled()        
        
    def track_orders(self):

        trades = self.exchange.recent_trades()

        sell = []
        buy = []

        for trade in trades:
            if trade['side'] == "Sell":
                sell.append(trade)
            else:
                buy.append(trade)

        for orders in self.buy_partially_filled:
            orignal_size = orders["orderQty"]
            for i in range(0, len(sell)):
                temp = sell[i]
                temp["timestamp"] = temp["timestamp"].replace('T', " ").replace('Z', "")
                temp_date = datetime.datetime.strptime(temp["timestamp"], '%Y-%m-%d %H:%M:%S.%f')
                if self.timestamp == None or temp_date >= self.timestamp:
                    if orders["price"] >= temp["price"] and temp["size"] > 0:
                        if (orignal_size - orders["cumQty"]) >= temp["size"]:
                            orders["orderQty"] = orignal_size
                            orders["cumQty"] = orders["cumQty"] + temp["size"]
                            self.calculate_position(orders, temp["size"])
                            orders["leavesQty"] = orders["orderQty"] - orders["cumQty"]
                            #self.insert_to_log("Order Partially Filled - ID:" + str(orders["orderID"]) + " " + orders["side"] + " " + str(orders["cumQty"]) + " @ " + str(orders["price"]) + " " + " Total size: " + str(orignal_size) + " By Trade: " + str(temp["size"]) + " @ " + str(temp["price"]) + " " + str(temp["timestamp"]))
                            self.timestamp = temp_date
                            orders['timestamp'] = self.exchange.current_timestamp().isoformat()
                            order_out = {
                            'status' : 'Partially Filled',
                            'PAPERTRADING' : self.settings.PAPERTRADING,
                            'type' : 'Paper',
                            'agress' : False,
                            'fillprice' : temp["price"],
                            'fillsize' : temp["size"], 
                            'data' : orders
                            }
                            self.pt_logger.info(json.dumps(order_out))
                            temp["size"] = 0
                        else:
                            #self.insert_to_log("Order Filled - ID:" + str(orders["orderID"]) + " " + orders["side"] + " " + str(orignal_size) + " @ " + str(orders["price"]) + " By Trade: " + str(temp["size"]) + " @ " + str(temp["price"]) + " " + str(temp["timestamp"]))
                            temp["size"] = temp["size"] - (orignal_size - orders["cumQty"])
                            self.calculate_position(orders, (orders["orderQty"] - orders["cumQty"]))
                            orders["orderQty"] = orignal_size
                            orders["cumQty"] = orignal_size
                            orders["leavesQty"] = 0
                            self.timestamp = temp_date
                            orders['timestamp'] = self.exchange.current_timestamp().isoformat()
                            order_out = {
                            'status' : 'Filled',
                            'PAPERTRADING' : self.settings.PAPERTRADING,
                            'type' : 'Paper',
                            'agress' : False,
                            'fillprice' : temp["price"],
                            'fillsize' : orders["orderQty"] - orders["cumQty"], 
                            'data' : orders
                            }
                            self.pt_logger.info(json.dumps(order_out))
                            break

        for orders in self.sell_partially_filled:
            orignal_size = orders["orderQty"]
            for i in range(0, len(buy)):
                temp = buy[i]
                temp["timestamp"] = temp["timestamp"].replace('T', " ").replace('Z', "")
                temp_date = datetime.datetime.strptime(temp["timestamp"], '%Y-%m-%d %H:%M:%S.%f')
                if self.timestamp == None or temp_date >= self.timestamp:
                    if orders["price"] <= temp["price"] and temp["size"] > 0:
                        if (orignal_size - orders["cumQty"]) >= temp["size"]:
                            orders["orderQty"] = orignal_size
                            orders["cumQty"] = orders["cumQty"] + temp["size"]
                            self.calculate_position(orders, temp["size"])
                            orders["leavesQty"] = orders["orderQty"] - orders["cumQty"]
                            #self.insert_to_log("Order Partially Filled - ID:" + str(orders["orderID"]) + " " + orders["side"] + " " + str(orders["cumQty"]) + " @ " + str(orders["price"]) + " " + " Total size: " + str(orignal_size) + " By Trade: " + str(temp["size"]) + " @ " + str(temp["price"]) + " " +  str(temp["timestamp"]))
                            orders['timestamp'] = self.exchange.current_timestamp().isoformat()
                            order_out = {
                            'status' : 'Partially Filled',
                            'PAPERTRADING' : self.settings.PAPERTRADING,
                            'type' : 'Paper',
                            'agress' : False,
                            'fillprice' : temp["price"],
                            'fillsize' : temp["size"],
                            'data' : orders
                            }
                            self.pt_logger.info(json.dumps(order_out))
                            temp["size"] = 0
                            self.timestamp = temp_date
                        else:
                            #self.insert_to_log("Order Filled - ID:" + str(orders["orderID"]) + " " + orders["side"] + " " + str(orignal_size) + " @ " + str(orders["price"]) + " By Trade: " + str(temp["size"]) + " @ " + str(temp["price"]) + " " + str(temp["timestamp"]))
                            temp["size"] = temp["size"] - (orignal_size - orders["cumQty"])
                            self.calculate_position(orders, (orders["orderQty"] - orders["cumQty"]))
                            orders["orderQty"] = orignal_size
                            orders["cumQty"] = orignal_size
                            orders["leavesQty"] = 0
                            self.timestamp = temp_date
                            orders['timestamp'] = self.exchange.current_timestamp().isoformat()
                            order_out = {
                            'status' : 'Filled',
                            'PAPERTRADING' : self.settings.PAPERTRADING,
                            'type' : 'Paper',
                            'fillprice' : temp["price"],
                            'fillsize' : orders["orderQty"] - orders["cumQty"], 
                            'data' : orders
                            }
                            self.pt_logger.info(json.dumps(order_out))
                            break

        self.from_partially_to_filled()

    def from_partially_to_filled(self):

        auxclone = copy.deepcopy(self.buy_partially_filled)
        deleted_ones = 0

        for i in range(0, len(auxclone)):
            if auxclone[i]["orderQty"] == auxclone[i]["cumQty"]:
                self.filled.append(auxclone[i])
                del self.buy_partially_filled[i - deleted_ones]
                deleted_ones += 1

        auxclone = copy.deepcopy(self.sell_partially_filled)
        deleted_ones = 0

        for i in range(0, len(auxclone)):
            if auxclone[i]["orderQty"] == auxclone[i]["cumQty"]:
                self.filled.append(auxclone[i])
                del self.sell_partially_filled[i - deleted_ones]
                deleted_ones += 1

    def from_partially_to_filled2(self):
        buy_temp = []
        for order in self.buy_partially_filled:
            if order["orderQty"] == order["cumQty"]:
                acopy = copy.deepcopy(order)
                self.filled.append(acopy)
            else:
                buy_temp.append(order)
        self.buy_partially_filled = buy_temp 
        
        sell_temp = []
        for order in self.sell_partially_filled:
            if order["orderQty"] == order["cumQty"]:
                acopy = copy.deepcopy(order)
                self.filled.append(acopy)
            else:
                sell_temp.append(order)
        self.sell_partially_filled = sell_temp
        
    

    def get_funds(self):

        return {"marginBalance": (self.settings.DRY_BTC + self.auxFunds) * constants.XBt_TO_XBT}

    def get_position(self, symbol):

        self.position["symbol"] = symbol
        return self.position

    def close_positions(self):


        auxsum = 0
        #auxpriceBuy = 0
        #auxpriceSell = 0
        #sumBuy = 0
        #sumSell = 0
        auxlist = copy.deepcopy(self.filled)
        to_delete = 0
        last_insert = 0
        #ticker = self.exchange.get_ticker()
        for i in range(0, len(auxlist)):

            Q = auxlist[i]

            if Q["side"] == "Buy":
                auxsum = auxsum + Q["orderQty"]
                #auxpriceBuy = auxpriceBuy + (Q["orderQty"] * Q["price"])
                #sumBuy += Q["orderQty"]

            else:
                auxsum = auxsum - Q["orderQty"]
                #auxpriceSell = auxpriceSell + (Q["orderQty"] * Q["price"])
                #sumSell += Q["orderQty"]

            to_delete += 1

            if auxsum == 0:
                #BuyFinal = auxpriceBuy / sumBuy
                #SellFinal = auxpriceSell / sumSell
                #self.auxFunds += ((SellFinal - BuyFinal) * sumBuy) / ticker["mid"]
                #auxpriceBuy = 0
                #auxpriceSell = 0
                #sumBuy = 0
                #sumSell = 0
                for j in range(last_insert, i + 1):
                    self.closed.append(auxlist[j])
                for j in range(0, to_delete):
                    del self.filled[0]
                to_delete = 0
                last_insert = i + 1

    def loop_functions(self):

        #self.track_orders()
        self.simulate_fills_from_trades()
        self.close_positions()

    def current_contract(self):
        count = 0

        for orders in self.filled:
            if orders["side"] == "Buy":
                count += orders["orderQty"]
            else:
                count -= orders["orderQty"]

        for orders in self.buy_partially_filled:
            count += orders["cumQty"]

        for orders in self.sell_partially_filled:
            count -= orders["cumQty"]

        return count

    def contract_traded_this_run(self):
        count = 0

        for orders in self.filled:
            count += orders["orderQty"]

        for orders in self.buy_partially_filled:
            count += orders["cumQty"]

        for orders in self.sell_partially_filled:
            count += orders["cumQty"]

        for orders in self.closed:
            count += orders["orderQty"]

        return count

    def insert_to_log(self, action):

        file = open("trading_log.txt", "a+")
        file.write(str(datetime.datetime.now()) + " - INFO - paper_trading" + action + "\n")
        file.close()

    def get_orders(self):

        final = []

        for orders in self.buy_partially_filled:
            final.append(orders)

        for orders in self.sell_partially_filled:
            final.append(orders)

        return final

    def cancel_order(self, orderID):

        for i in range(0, len(self.buy_partially_filled)):
            if self.buy_partially_filled[i]["orderID"] == orderID:
                if self.buy_partially_filled[i]["cumQty"] > 0:
                    self.buy_partially_filled[i]["orderQty"] = self.buy_partially_filled[i]["cumQty"]
                    self.filled.append(self.buy_partially_filled[i])
                #self.insert_to_log(" Cancelling - ID:" + str(self.buy_partially_filled[i]["orderID"]) + " " + self.buy_partially_filled[i]["side"] + " " + str(self.buy_partially_filled[i]["orderQty"]) + " @ " + str(self.buy_partially_filled[i]["price"]))
                self.buy_partially_filled[i]['timestamp'] = self.exchange.current_timestamp().isoformat()
                order_out = {
                'status' : 'Cancelled',
                'PAPERTRADING' : self.settings.PAPERTRADING,
                'type' : 'Paper',
                'data' : self.buy_partially_filled[i]
                }
                self.pt_logger.info(json.dumps(order_out))
                del self.buy_partially_filled[i]
                break

        for i in range(0, len(self.sell_partially_filled)):
            if self.sell_partially_filled[i]["orderID"] == orderID:
                if self.sell_partially_filled[i]["cumQty"] > 0:
                    self.sell_partially_filled[i]["orderQty"] = self.sell_partially_filled[i]["cumQty"]
                    self.filled.append(self.sell_partially_filled[i])
                #self.insert_to_log(" Cancelling - ID:" + str(self.sell_partially_filled[i]["orderID"]) + " " + self.sell_partially_filled[i]["side"] + " " + str(self.sell_partially_filled[i]["orderQty"]) + " @ " + str(self.sell_partially_filled[i]["price"]))
                self.sell_partially_filled[i]['timestamp'] = self.exchange.current_timestamp().isoformat()
                order_out = {
                'status' : 'Cancelled',
                'PAPERTRADING' : self.settings.PAPERTRADING,
                'type' : 'Paper',
                'data' : self.sell_partially_filled[i]
                }
                self.pt_logger.info(json.dumps(order_out))
                del self.sell_partially_filled[i]
                break

    def cancel_all_orders(self):

        cloneBuy = copy.deepcopy(self.buy_partially_filled)

        cloneSell = copy.deepcopy(self.sell_partially_filled)

        for order in cloneBuy:
            self.cancel_order(order["orderID"])

        for order in cloneSell:
            self.cancel_order(order["orderID"])

    def calculate_position(self, order, Qty):

        clone = Qty

        if order["side"] == "Sell":
            clone *= -1

        if self.position["currentQty"] == 0:
            self.position = {'avgCostPrice': order["price"], 'avgEntryPrice': order["price"], 'currentQty': clone, 'symbol': "XBTUSD"}
            return

        if (self.position["currentQty"] < 0 and clone < 0) or (self.position["currentQty"] > 0 and clone > 0):

            newQTY = self.position["currentQty"] + clone

            newPrice = (self.position["avgEntryPrice"] * self.position["currentQty"]) + (clone * order["price"])

            self.position["avgCostPrice"] = newPrice / newQTY
            self.position["avgEntryPrice"] = newPrice / newQTY
            self.position["currentQty"] = newQTY

        else:

            if self.position["currentQty"] > Qty:

                    if self.position["currentQty"] > 0:
                        profit = ((1/self.position["avgEntryPrice"]) - (1/order["price"])) * Qty
                    else:
                        profit = ((1/order["price"]) - (1/self.position["avgEntryPrice"])) * Qty

                    self.position["currentQty"] = self.position["currentQty"] + clone

                    self.auxFunds += profit

            else:

                if self.position["currentQty"] > 0:
                    profit = ((1/self.position["avgEntryPrice"]) - (1/order["price"])) * abs(self.position["currentQty"])
                else:
                    profit = ((1/order["price"]) - (1/self.position["avgEntryPrice"])) * abs(self.position["currentQty"])

                self.position["avgEntryPrice"] = order["price"]
                self.position["avgCostPrice"] = order["price"]
                self.position["currentQty"] = self.position["currentQty"] + clone

                self.auxFunds += profit







