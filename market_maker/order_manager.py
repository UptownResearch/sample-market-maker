from __future__ import absolute_import

import atexit
import json
import os
import signal
import sys
from math import floor, ceil
# Find code directory relative to our directory
from os.path import dirname, abspath, join
from os.path import getmtime
from time import sleep

import requests

THIS_DIR = dirname(__file__)
#CODE_DIR = abspath(join(THIS_DIR, '..', '..' ))
CODE_DIR = abspath(join(THIS_DIR, '..' ))
sys.path.insert(0, CODE_DIR)
#sys.path.append(CODE_DIR)

#from market_maker import bitmex
#from market_maker.settings import settings
from market_maker.utils import constants, errors, math
#from market_maker import PAPERTRADING_tracker
from market_maker.exchange_interface import ExchangeInterface
#from market_maker.coinbase.order_book import OrderBook

import logging
import random

# Used for reloading the bot - saves modified times of key files
#import os
#watched_files_mtimes = [(f, getmtime(f)) for f in self.settings.WATCHED_FILES]

#
# Helpers
#
#logger = log.setup_custom_logger('root')
logger = logging.getLogger("root")

compare_logger = logging.getLogger("PAPERTRADING")
compare_logger.setLevel(logging.WARN)
#fh = logging.FileHandler("PAPERTRADING_logger.log")
#formatter = logging.Formatter(
#    '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
#fh.setFormatter(formatter)
#compare_logger.addHandler(fh)

class OrderManager:
    def __init__(self, orders_logging_file = None, settings = None, exchange = None):
        self.settings = settings
        if exchange == None:
            self.exchange = ExchangeInterface(self.settings.DRY_RUN, settings=self.settings)
        else:
            self.exchange = exchange
        #if not self.settings.BACKTEST:
        #    self.coinbase = OrderBook(product_id='BTC-USD')
        #    self.coinbase.start()
        # Once exchange is created, register exit handler that will always cancel orders
        # on any error.
        atexit.register(self.exit)
        signal.signal(signal.SIGTERM, self.exit)

        logger.info("Using symbol %s." % self.exchange.symbol)

        if self.settings.DRY_RUN:
            logger.info("Initializing dry run. Orders printed below represent what would be posted to BitMEX.")
        else:
            logger.info("Order Manager initializing, connecting to BitMEX. Live run: executing real trades.")
            compare_logger.info("Order Manager initializing, connecting to BitMEX. Live run: executing real trades.")

        #self.start_time = self.exchange.current_timestep()
        self.instrument = self.exchange.get_instrument()
        if self.settings.compare is True:
            self.starting_qty = self.exchange.get_delta()[0]
        else:
            self.starting_qty = self.exchange.get_delta()
        self.running_qty = self.starting_qty
        self.reset()
        self.amend_error_counter = 0
        self.cancelled_orders = []


    def close_log_files(self):
        #handlers = self.pt_logger.handlers[:]
        #for handler in handlers:
        #    handler.close()
        #    self.pt_logger.removeHandler(handler)
        pass

    def reset(self):
        self.exchange.cancel_all_orders()
        if self.settings.get('SANITY_CHECK', True):
            self.sanity_check()
        self.print_status()

        # Create orders and converge.
        # Suspect that creating orders outside of loop is causing issues in backtests
        # self.place_orders()

    def ceilNearest(self, amount, roundAmount):
        return ceil(amount * (1.0 / roundAmount)) / (1.0 / roundAmount)

    def floorNearest(self, amount, roundAmount):
        return floor(amount * (1.0 / roundAmount)) / (1.0 / roundAmount)

    def print_status(self):
        #don't print status if backtesting
        if self.settings.BACKTEST is True:
            return

        """Print the current MM status."""

        margin = self.exchange.get_margin()
        position = self.exchange.get_position()
        self.running_qty = self.exchange.get_delta()
        tickLog = self.exchange.get_instrument()['tickLog']
        self.start_XBt = margin["marginBalance"]

        logger.info("Current XBT Balance: %.6f" % XBt_to_XBT(self.start_XBt))
        logger.info("Current Contract Position: %d" % self.running_qty)
        if self.settings.CHECK_POSITION_LIMITS:
            logger.info("Position limits: %d/%d" % (self.settings.MIN_POSITION, self.settings.MAX_POSITION))
        if position['currentQty'] != 0:
            logger.info("Avg Cost Price: %.*f" % (tickLog, float(position['avgCostPrice'])))
            logger.info("Avg Entry Price: %.*f" % (tickLog, float(position['avgEntryPrice'])))
        logger.info("Contracts Traded This Run: %d" % (self.exchange.contracts_this_run() - self.starting_qty))
        logger.info("Total Contract Delta: %.4f XBT" % self.exchange.calc_delta()['spot'])

    def get_ticker(self):
        ticker = self.exchange.get_ticker()
        tickLog = self.exchange.get_instrument()['tickLog']

        # Set up our buy & sell positions as the smallest possible unit above and below the current spread
        # and we'll work out from there. That way we always have the best price but we don't kill wide
        # and potentially profitable spreads.
        self.start_position_buy = ticker["buy"] + self.instrument['tickSize']
        self.start_position_sell = ticker["sell"] - self.instrument['tickSize']

        # If we're maintaining spreads and we already have orders in place,
        # make sure they're not ours. If they are, we need to adjust, otherwise we'll
        # just work the orders inward until they collide.
        if self.settings.MAINTAIN_SPREADS:
            if ticker['buy'] == self.exchange.get_highest_buy()['price']:
                self.start_position_buy = ticker["buy"]
            if ticker['sell'] == self.exchange.get_lowest_sell()['price']:
                self.start_position_sell = ticker["sell"]

        # Back off if our spread is too small.
        if self.start_position_buy * (1.00 + self.settings.MIN_SPREAD) > self.start_position_sell:
            self.start_position_buy *= (1.00 - (self.settings.MIN_SPREAD / 2))
            self.start_position_sell *= (1.00 + (self.settings.MIN_SPREAD / 2))

        # Midpoint, used for simpler order placement.
        self.start_position_mid = ticker["mid"]
        logger.info(
            "%s Ticker: Buy: %.*f, Sell: %.*f" %
            (self.instrument['symbol'], tickLog, ticker["buy"], tickLog, ticker["sell"])
        )
        compare_logger.info(
            "%s Ticker: Buy: %.*f, Sell: %.*f" %
            (self.instrument['symbol'], tickLog, ticker["buy"], tickLog, ticker["sell"])
        )
        logger.info('Start Positions: Buy: %.*f, Sell: %.*f, Mid: %.*f' %
                    (tickLog, self.start_position_buy, tickLog, self.start_position_sell,
                     tickLog, self.start_position_mid))
        compare_logger.info('Start Positions: Buy: %.*f, Sell: %.*f, Mid: %.*f' %
                    (tickLog, self.start_position_buy, tickLog, self.start_position_sell,
                     tickLog, self.start_position_mid))
        return ticker

    def get_price_offset(self, index):
        """Given an index (1, -1, 2, -2, etc.) return the price for that side of the book.
           Negative is a buy, positive is a sell."""
        # Maintain existing spreads for max profit
        if self.settings.MAINTAIN_SPREADS:
            start_position = self.start_position_buy if index < 0 else self.start_position_sell
            # First positions (index 1, -1) should start right at start_position, others should branch from there
            index = index + 1 if index < 0 else index - 1
        else:
            # Offset mode: ticker comes from a reference exchange and we define an offset.
            start_position = self.start_position_buy if index < 0 else self.start_position_sell

            # If we're attempting to sell, but our sell price is actually lower than the buy,
            # move over to the sell side.
            if index > 0 and start_position < self.start_position_buy:
                start_position = self.start_position_sell
            # Same for buys.
            if index < 0 and start_position > self.start_position_sell:
                start_position = self.start_position_buy

        return math.toNearest(start_position * (1 + self.settings.INTERVAL) ** index, self.instrument['tickSize'])

    ###
    # Orders
    ###

    def place_orders(self):
        """Create order items for use in convergence."""

        buy_orders = []
        sell_orders = []
        # Create orders from the outside in. This is intentional - let's say the inner order gets taken;
        # then we match orders from the outside in, ensuring the fewest number of orders are amended and only
        # a new order is created in the inside. If we did it inside-out, all orders would be amended
        # down and a new order would be created at the outside.
        for i in reversed(range(1, self.settings.ORDER_PAIRS + 1)):
            if not self.long_position_limit_exceeded():
                buy_orders.append(self.prepare_order(-i))
            if not self.short_position_limit_exceeded():
                sell_orders.append(self.prepare_order(i))

        return self.converge_orders(buy_orders, sell_orders)

    def prepare_order(self, index):
        """Create an order object."""

        if self.settings.RANDOM_ORDER_SIZE is True:
            quantity = random.randint(self.settings.MIN_ORDER_SIZE, self.settings.MAX_ORDER_SIZE)
        else:
            quantity = self.settings.ORDER_START_SIZE + ((abs(index) - 1) * self.settings.ORDER_STEP_SIZE)

        price = self.get_price_offset(index)

        return {'price': price, 'orderQty': quantity, 'side': "Buy" if index < 0 else "Sell"}

    def get_order_with_role(self, orders, role):
        '''Return first order with role.'''
        for order in orders:
            if order.get('side', "") == role and \
                order.get('orderID', "") not in self.cancelled_orders and \
                order.get('ordStatus', "") not in  ['Filled', 'Canceled']:
                return order
        return None

    def get_all_orders_with_role(self, orders, role):
        '''Return all orders with role.'''
        ret_orders = []
        for order in orders:
            if order.get('side', "") == role and \
                order.get('orderID', "") not in self.cancelled_orders and \
                order.get('ordStatus', "") not in  ['Filled', 'Canceled']:
                ret_orders.append(order)
        return ret_orders

    def cancel_all_orders(self, existing_orders):
        for order in existing_orders:
            if 'orderID' not in order:
                logger.warning("Can't Cancel - No orderID in order: %s" % json.dumps(order))
                continue
            if isinstance(order['orderID'], int):
                logger.warning("Can't Cancel - OrderID length must be 36 characters: %s" % json.dumps(order))
                continue
            self.exchange.cancel_order(order)
            self.cancelled_orders.append(order['orderID'])
        self.exchange.cancel_all_orders()

    def cancel_orders(self, orders):
        for order in orders:
            if 'orderID' not in order:
                logger.warning("Can't Cancel - No orderID in order: %s" % json.dumps(order))
                continue
            if isinstance(order['orderID'], int):
                logger.warning("Can't Cancel - OrderID must be a string (perhaps you are canceling a promised order: %s" % \
                            json.dumps(order))
                continue
            self.exchange.cancel_order(order)
            self.cancelled_orders.append(order['orderID'])

    def create_cancel_orders_from_orders(self, orders):
        to_cancel = []
        for order in orders:
            if not self.is_live_order(order):
                logger.warning("Waiting to cancel order: %s" % json.dumps(order))
                continue
            the_keys = ['orderID', 'side', 'orderQty', 'price']
            order_to_cancel = dict((key, value) for key, value in \
                                   order.items() if key in the_keys)
            to_cancel.append(order_to_cancel)
        return to_cancel

    def is_live_order(self, order):
        ''' Checks order for liveness. Liveness means that it is an order confirmed
        by the exchange to be live, and is not a promise created by exchange_interface to
        record an expected live order.'''
        if order.get('ordStatus', "") not in ['Filled', 'Canceled'] and \
                'submission_time' not in order and 'ordStatus' in order:
            return True
        else:
            return False

    def live_orders(self, orders):
        '''Tries to determine orders that are live on the exchange.'''
        ret_orders = []
        for order in orders:
            if self.is_live_order(order):
                ret_orders.append(order)
        return ret_orders

    def desired_to_orders(self, buyprice, sellprice,
        buyamount = 100, sellamount = 100, tags = {}):
        tickLog = self.exchange.get_instrument()['tickLog']
        existing_orders = self.exchange.get_orders()
        to_create = []
        to_amend = []
        to_cancel = []

        # Perform some initial checks
        if len(self.live_orders(existing_orders)) > 4:
            logger.warning("Number of orders exceeds 4, canceling all orders")
            self.cancel_orders(existing_orders)
            return

        # Manage Buy Order
        buy_orders = self.get_all_orders_with_role(existing_orders, 'Buy')
        if buy_orders != []:
            buy_order = buy_orders[0]
            if len(buy_orders) > 1:
                # cancel all orders above 1
                to_cancel.extend(self.create_cancel_orders_from_orders(buy_orders[1:]))

            # If a recently submitted order, let's not amend
            if self.is_live_order(buy_order) and buy_order['price'] != buyprice:
                the_keys = ['orderID', 'side']
                amended_order = dict((key, value) for key, value in \
                                     buy_order.items() if key in the_keys)
                amended_order['price'] = buyprice
                amended_order['orderQty'] = buyamount
                amended_order.update(tags)
                if amended_order['orderQty'] > 0:
                        to_amend.append(amended_order)
        else:
            # let's create a new order
            buyorder = {'price': buyprice, 'orderQty': buyamount, 'side': "Buy",
                        'orderID': random.randint(0, 100000)}
            buyorder.update(tags)
            if buyorder['orderQty'] > 0:
                to_create.append(buyorder)

        # Manage Sell Order
        sell_orders = self.get_all_orders_with_role(existing_orders, 'Sell')
        if sell_orders != []:
            sell_order = sell_orders[0]
            if len(sell_orders) > 1:
                # cancel all orders above 1
                to_cancel.extend(self.create_cancel_orders_from_orders(sell_orders[1:]))
            # If a recently submitted order, let's not amend
            if self.is_live_order(sell_order) and sell_order['price'] != sellprice:
                the_keys = ['orderID', 'side']
                amended_order = dict((key, value) for key, value in \
                                     sell_order.items() if key in the_keys)
                amended_order['price'] = sellprice
                amended_order['orderQty'] = sellamount
                amended_order.update(tags)
                if amended_order['orderQty'] > 0:
                        to_amend.append(amended_order)
        else:
            # let's create a new order
            sellorder = {'price':  sellprice, 'orderQty': sellamount, 'side': "Sell",
            'orderID': random.randint(0, 100000)}
            sellorder.update(tags)
            if sellorder['orderQty'] > 0:
                to_create.append(sellorder)

        # Amend orders as needed
        if len(to_amend) > 0:
            self.amend_orders( to_amend, existing_orders)
        # Create any needed new orders
        if len(to_create) > 0:
            self.create_new_orders(to_create)
        # Cancel any needed orders
        if len(to_cancel) > 0:
            self.cancel_orders(to_cancel)

    def create_new_orders(self, to_create):
        tickLog = self.exchange.get_instrument()['tickLog']
        logger.info("Creating %d orders:" % (len(to_create)))
        #compare_logger.info("Creating %d orders:" % (len(to_create)))
        for order in reversed(to_create):
            logger.info("%4s %d @ %.*f" % (order['side'], order['orderQty'], tickLog, order['price']))
            #compare_logger.info("%4s %d @ %.*f" % (order['side'], order['orderQty'], tickLog, order['price']))
        self.exchange.create_bulk_orders(to_create)


    def amend_orders(self, to_amend, existing_orders):
        tickLog = self.exchange.get_instrument()['tickLog']
        logger.info("Amending Orders %s" % json.dumps(to_amend))
        for amended_order in reversed(to_amend):
            reference_order = [o for o in existing_orders if o['orderID'] == amended_order['orderID']][0]
            # Below is commented out because 'leavesQty is not available for CCXT
            #logger.info("Amending %4s: %d @ %.*f to %d @ %.*f (%+.*f)" % (
            #    amended_order['side'],
            #    reference_order['leavesQty'], tickLog, reference_order['price'],
            #    (amended_order['orderQty'] - reference_order['cumQty']), tickLog, amended_order['price'],
            #    tickLog, (amended_order['price'] - reference_order['price'])
            #))
        # This can fail if an order has closed in the time we were processing.
        # The API will send us `invalid ordStatus`, which means that the order's status (Filled/Canceled)
        # made it not amendable.
        # If that happens, we need to catch it and re-tick.
        try:
            self.exchange.amend_bulk_orders(to_amend)
        except requests.exceptions.HTTPError as e:
            errorObj = e.response.json()
            if errorObj['error']['message'] == 'Invalid ordStatus':
                logger.warn("Amending failed. Waiting for order data to converge and retrying.")
                logger.warn("Failed on orders: %s" % json.dumps(to_amend))
                for order in to_amend:
                    self.cancelled_orders.append(order['orderID'])
                #try:
                #    self.cancel_orders(to_amend)
                #except:
                #    logger.warn("Couldn't cancel orders!: %s" % json.dumps(to_amend))
                #    raise
                # sleep(0.5)
                # return self.place_orders()
                self.amend_error_counter += 1
            else:
                logger.error("Unknown error on amend: %s. Exiting" % errorObj)
                sys.exit(1)
        except ValueError as e:
            logger.error('Failed to amend order (Ignoring amend request): ' + str(e))

    def prices_to_orders(self, buyprice, sellprice, buyamount = 100, sellamount = 100, theo=-1):
        tickLog = self.exchange.get_instrument()['tickLog']
        to_amend = []
        to_create = []
        existing_orders = self.exchange.get_orders()
        if len(existing_orders) > 4:
            logger.warning("Number of orders exceeds 4, canceling all orders")
            self.exchange.cancel_all_orders()
            return
        if self.amend_error_counter > 5:
            logger.warning('Number of amend failures exceeds 5, canceling all orders')
            self.amend_error_counter = 0
            self.exchange.cancel_all_orders()
            return
        buy_present = sell_present = False
        try:
            last_price = self.exchange.recent_trades()[-1]['price']
        except:
            last_price = 0.0
        coinbase_midprice = 0.0
        if not self.settings.BACKTEST:
            try:
                #coinbase_midprice = float(self.coinbase.get_bid()+self.coinbase.get_ask())/2
                pass
            except:
                pass

        if theo < 0:
            midprice = last_price #ticker["mid"]
        else:
            midprice = theo
        if len(existing_orders) > 1:
            for order in existing_orders:
                if 'submission_time' in order:
                    continue
                if order['side'] == "Buy":
                    if order['price'] != buyprice:                     
                        neworder = {'orderID': order['orderID'], 
                                    'orderQty': buyamount, 'price': buyprice, 'side': "Buy", 
                                    'theo': midprice, 'last_price':last_price}
                        if not buy_present:     
                            buy_present = True
                            if neworder['orderQty'] > 0:
                                to_amend.append(neworder)
                        else:
                            #neworder['orderQty'] = 0
                            pass
                    else:
                        buy_present = True
                    

                else:
                    if order['price'] != sellprice:
                        neworder = {'orderID': order['orderID'], 
                                    'orderQty': sellamount, 'price':  sellprice, 'side': "Sell" , 
                                    'theo': midprice, 'last_price':last_price}
                        if not sell_present:     
                            sell_present = True
                            if neworder['orderQty'] > 0:
                                to_amend.append(neworder)
                        else:
                            #neworder['orderQty'] = 0
                            pass
                    else:
                        sell_present = True

        elif len(existing_orders) == 1:
            for order in existing_orders:
                side = "Buy" if order['side'] == "Sell" else "Sell"
                size = buyamount if order['side'] == "Sell" else sellamount 
                price = buyprice if order['side'] == "Sell" else sellprice
                neworder = {'price':  price, 'orderQty': size, 'side': side, 'theo': midprice, 'last_price':last_price, 'orderID': random.randint(0, 100000)  }
                if neworder['orderQty'] > 0:
                    to_create.append(neworder)
        else:
            #cancel existing orders and create new ones
            logger.info("Length of existing orders: %d" % (len(existing_orders)))
            self.exchange.cancel_all_orders()
            buyorder = {'price':  buyprice, 'orderQty': buyamount, 'side': "Buy", 'theo': midprice, 'last_price':last_price, 'orderID': random.randint(0, 100000)  }
            sellorder = {'price':  sellprice, 'orderQty': sellamount, 'side': "Sell", 'theo': midprice, 'last_price':last_price, 'orderID': random.randint(0, 100000) }
            if buyorder['orderQty'] > 0:
                to_create.append(buyorder)
            if sellorder['orderQty'] > 0: 
                to_create.append(sellorder)

        # Amend orders as needed
        if len(to_amend) > 0:
            self.amend_orders(to_amend, existing_orders)
        # Create any needed new orders
        if len(to_create) > 0:
            self.create_new_orders(to_create)



    def converge_orders(self, buy_orders, sell_orders):
        """Converge the orders we currently have in the book with what we want to be in the book.
           This involves amending any open orders and creating new ones if any have filled completely.
           We start from the closest orders outward."""

        tickLog = self.exchange.get_instrument()['tickLog']
        to_amend = []
        to_create = []
        to_cancel = []
        buys_matched = 0
        sells_matched = 0
        existing_orders = self.exchange.get_orders()

        # Check all existing orders and match them up with what we want to place.
        # If there's an open one, we might be able to amend it to fit what we want.
        for order in existing_orders:
            try:
                if order['side'] == 'Buy':
                    desired_order = buy_orders[buys_matched]
                    buys_matched += 1
                else:
                    desired_order = sell_orders[sells_matched]
                    sells_matched += 1

                # Found an existing order. Do we need to amend it?
                if desired_order['orderQty'] != order['leavesQty'] or (
                        # If price has changed, and the change is more than our RELIST_INTERVAL, amend.
                        desired_order['price'] != order['price'] and
                        abs((desired_order['price'] / order['price']) - 1) > self.settings.RELIST_INTERVAL):
                    
                    # The math in this next line seems wrong. Instead of the new orderQty being 
                    # order['cumQty'] + desired_order['orderQty'], it seems like it should be
                    # desired_order['orderQty'] - order['cumQty']  
                    to_amend.append({'orderID': order['orderID'], 'orderQty': order['cumQty'] + desired_order['orderQty'],
                                     'price': desired_order['price'], 'side': order['side']})
            except IndexError:
                # Will throw if there isn't a desired order to match. In that case, cancel it.
                #to_cancel.append(order)
                pass

        while buys_matched < len(buy_orders):
            to_create.append(buy_orders[buys_matched])
            buys_matched += 1

        while sells_matched < len(sell_orders):
            to_create.append(sell_orders[sells_matched])
            sells_matched += 1

        if len(to_amend) > 0:
            for amended_order in reversed(to_amend):
                reference_order = [o for o in existing_orders if o['orderID'] == amended_order['orderID']][0]
                logger.info("Amending %4s: %d @ %.*f to %d @ %.*f (%+.*f)" % (
                    amended_order['side'],
                    reference_order['leavesQty'], tickLog, reference_order['price'],
                    (amended_order['orderQty'] - reference_order['cumQty']), tickLog, amended_order['price'],
                    tickLog, (amended_order['price'] - reference_order['price'])
                ))
            # This can fail if an order has closed in the time we were processing.
            # The API will send us `invalid ordStatus`, which means that the order's status (Filled/Canceled)
            # made it not amendable.
            # If that happens, we need to catch it and re-tick.
            try:
                self.exchange.amend_bulk_orders(to_amend)
            except requests.exceptions.HTTPError as e:
                errorObj = e.response.json()
                if errorObj['error']['message'] == 'Invalid ordStatus':
                    logger.warn("Amending failed. Waiting for order data to converge and retrying.")
                    sleep(0.5)
                    return self.place_orders()
                else:
                    logger.error("Unknown error on amend: %s. Exiting" % errorObj)
                    sys.exit(1)

        if len(to_create) > 0:
            logger.info("Creating %d orders:" % (len(to_create)))
            compare_logger.info("Creating %d orders:" % (len(to_create)))
            for order in reversed(to_create):
                logger.info("%4s %d @ %.*f" % (order['side'], order['orderQty'], tickLog, order['price']))
                compare_logger.info("%4s %d @ %.*f" % (order['side'], order['orderQty'], tickLog, order['price']))
            self.exchange.create_bulk_orders(to_create)

        # Could happen if we exceed a delta limit
        if len(to_cancel) > 0:
            logger.info("Canceling %d orders:" % (len(to_cancel)))
            for order in reversed(to_cancel):
                logger.info("%4s %d @ %.*f" % (order['side'], order['leavesQty'], tickLog, order['price']))
                compare_logger.info("%4s %d @ %.*f" % (order['side'], order['leavesQty'], tickLog, order['price']))
            self.exchange.cancel_bulk_orders(to_cancel)

    ###
    # Position Limits
    ###

    def short_position_limit_exceeded(self):
        """Returns True if the short position limit is exceeded"""
        if not self.settings.CHECK_POSITION_LIMITS:
            return False
        position = self.exchange.get_delta()
        return position <= self.settings.MIN_POSITION

    def long_position_limit_exceeded(self):
        """Returns True if the long position limit is exceeded"""
        if not self.settings.CHECK_POSITION_LIMITS:
            return False
        position = self.exchange.get_delta()
        return position >= self.settings.MAX_POSITION

    ###
    # Sanity
    ##

    def sanity_check(self):
        """Perform checks before placing orders."""

        # Check if OB is empty - if so, can't quote.
        self.exchange.check_if_orderbook_empty()

        # Ensure market is still open.
        self.exchange.check_market_open()

        # Get ticker, which sets price offsets and prints some debugging info.
        ticker = self.get_ticker()

        # Sanity check:
        #if self.get_price_offset(-1) >= ticker["sell"] or self.get_price_offset(1) <= ticker["buy"]:
        #    logger.error("Buy: %s, Sell: %s" % (self.start_position_buy, self.start_position_sell))
        #    logger.error("First buy position: %s\nBitMEX Best Ask: %s\nFirst sell position: %s\nBitMEX Best Bid: %s" %
        #                 (self.get_price_offset(-1), ticker["sell"], self.get_price_offset(1), ticker["buy"]))
        #    logger.error("Sanity check failed, exchange data is inconsistent")
        #    self.exit()

        # Messaging if the position limits are reached
        if self.long_position_limit_exceeded():
            logger.info("Long delta limit exceeded")
            logger.info("Current Position: %.f, Maximum Position: %.f" %
                        (self.exchange.get_delta(), self.settings.MAX_POSITION))

        if self.short_position_limit_exceeded():
            logger.info("Short delta limit exceeded")
            logger.info("Current Position: %.f, Minimum Position: %.f" %
                        (self.exchange.get_delta(), self.settings.MIN_POSITION))

    ###
    # Running
    ###

    def check_file_change(self):
        """Restart if any files we're watching have changed."""
        for f, mtime in watched_files_mtimes:
            if getmtime(f) > mtime:
                self.restart()

    def check_connection(self):
        """Ensure the WS connections are still open."""
        return self.exchange.is_open()

    def exit(self):
        logger.info("Shutting down. All open orders will be cancelled.")
        try:
            self.exchange.cancel_all_orders()
            self.exchange.exit_exchange()
        except errors.AuthenticationError as e:
            logger.info("Was not authenticated; could not cancel orders.")
        except Exception as e:
            logger.info("Unable to cancel orders: %s" % e)

        sys.exit()

    def run_loop(self):
        print("Entered Run Loop.")
        def print_output():
            if not self.settings.BACKTEST:    
                sys.stdout.write("-----\n")
                sys.stdout.flush()
            self.print_status()

        while True:
            # Remove comment to drop system into debugger, great to understand issues 
            #import pdb; pdb.set_trace()
            if self.settings.BACKTEST:
                try:
                    self.exchange.wait_update()
                except EOFError:
                    self.close_log_files()
                    logger.info("Reached end of Backtest file.")
                    break
            if self.exchange.ok_to_enter_order():
                #self.check_file_change()
                #sleep(self.settings.LOOP_INTERVAL)
                if self.settings.get('SANITY_CHECK', True):
                    self.sanity_check()  # Ensures health of mm - several cut-out points here

                # This will restart on very short downtime, but if it's longer,
                # the MM will crash entirely as it is unable to connect to the WS on boot.

                self.place_orders()  # Creates desired orders and converges to existing orders
 
                if not self.settings.BACKTEST:
                    if not self.check_connection():
                        logger.error("Realtime data connection unexpectedly closed, restarting.")
                        self.restart()
                    self.print_status()  # Print skew, delta, etc
                else:
                    periodically_call(print_output, amount=200)
                #The following should now be taken care of by wait_update
                #self.exchange.loop()

    def restart(self):
        logger.info("Restarting the market maker...")
        os.execv(sys.executable, [sys.executable] + sys.argv)

#
# Helpers
#
def static_vars(**kwargs):
    def decorate(func):
        for k in kwargs:
            setattr(func, k, kwargs[k])
        return func
    return decorate

@static_vars(counter=0)
def periodically_call(afunc, amount=10):
    periodically_call.counter += 1
    if periodically_call.counter > amount:
        periodically_call.counter = 0
        return afunc()

def XBt_to_XBT(XBt):
    return float(XBt) / constants.XBt_TO_XBT


def cost(instrument, quantity, price):
    mult = instrument["multiplier"]
    P = mult * price if mult >= 0 else mult / price
    return abs(quantity * P)


def margin(instrument, quantity, price):
    return cost(instrument, quantity, price) * instrument["initMargin"]


def run():
    logger.info('BitMEX Market Maker Version: %s\n' % constants.VERSION)

    om = CustomOrderManager()
    # Try/except just keeps ctrl-c from printing an ugly stacktrace
    try:
        om.run_loop()
    except (KeyboardInterrupt, SystemExit):
        sys.exit()


class CustomOrderManager(OrderManager):
    """A sample order manager for implementing your own custom strategy"""
    onlyone = True
    def place_orders(self) -> None:
        # implement your custom strategy here

        # implement your custom strategy here

        buy_orders = []
        sell_orders = []
        ticker = self.exchange.get_ticker()
        mid = ticker["mid"]
        # populate buy and sell orders, e.g.
        if self.onlyone:
            buy_orders.append({'price': mid + 1, 'orderQty': 1000, 'side': "Buy"})
            sell_orders.append({'price': mid - 1, 'orderQty': 500, 'side': "Sell"})
            self.onlyone = False

        self.converge_orders(buy_orders, sell_orders)



