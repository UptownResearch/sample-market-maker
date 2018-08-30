from market_maker.paperless_tracker import paperless_tracker
import datetime
import iso8601
from unittest.mock import MagicMock
from market_maker.settings import settings

class ExchangeInterface:
    def current_timestamp(self):
        pass
    def market_depth(self, symbol):
        pass
    def recent_trades(self):
        pass





orderbook = \
[{'symbol': 'XBTUSD',
  'id': 15599351300,
  'side': 'Sell',
  'size': 1000,
  'price': 5001.5},
 {'symbol': 'XBTUSD',
  'id': 15599351350,
  'side': 'Sell',
  'size': 1000,
  'price': 5001},
 {'symbol': 'XBTUSD',
  'id': 15599351400,
  'side': 'Buy',
  'size': 1000,
  'price': 5000.5},
 {'symbol': 'XBTUSD',
  'id': 15599351450,
  'side': 'Buy',
  'size': 1000,
  'price': 5000}]

trades = [{'timestamp': '2018-08-09T20:11:57.000Z',
  'symbol': 'XBTUSD',
  'side': 'Sell',
  'size': 100,
  'price': 5000.5,
  'tickDirection': 'MinusTick',
  'trdMatchID': '5ff50e87-17e9-f86c-9464-e77d452637d1',
  'grossValue': 1999800,
  'homeNotional': 0.019998,
  'foreignNotional': 100},
 {'timestamp': '2018-08-09T20:11:56.000Z',
  'symbol': 'XBTUSD',
  'side': 'Buy',
  'size': 100,
  'price': 5001,
  'tickDirection': 'PlusTick',
  'trdMatchID': 'df7f3037-b303-08ea-0f3d-699cb42f15bb',
  'grossValue': 1999600,
  'homeNotional': 0.01999600,
  'foreignNotional': 100}]


class Test1(ExchangeInterface):
    
    def __init__(self):
        self.timestamp = iso8601.parse_date('2018-08-09T20:11:55.000Z')
        
    def current_timestamp(self):
        return self.timestamp
    def market_depth(self, symbol):
        return orderbook
    def recent_trades(self):
        return trades 

def test_get_paperless_tracker():
	pp_tracker = paperless_tracker.getInstance()
	assert pp_tracker is not None

def test_rest_order_in_book():
	settings = MagicMock()
	settings.BACKTEST = True
	order_rest_in_book = [{'price': 5000.5, 'orderQty': 100, 'side': "Buy"}]
	pp_tracker = paperless_tracker.getInstance()
	pp_tracker.reset()
	Test1Ei = Test1()
	pp_tracker.provide_exchange(Test1Ei)
	pp_tracker.track_orders_created(order_rest_in_book)
	assert len(pp_tracker.buy_orders_created)  == 1
	assert pp_tracker.buy_orders_created[0]['price'] == 5000.5
	assert pp_tracker.buy_orders_created[0]['orderQty'] == 100

def test_add_and_remove_order():
	order_rest_in_book = [{'price': 5000.5, 'orderQty': 100, 'side': "Buy"}]
	pp_tracker = paperless_tracker.getInstance()
	pp_tracker.reset()
	Test1Ei = Test1()
	pp_tracker.provide_exchange(Test1Ei)
	pp_tracker.track_orders_created(order_rest_in_book)
	assert len(pp_tracker.buy_orders_created)  == 1
	orders = pp_tracker.get_orders()
	for order in orders:
		pp_tracker.cancel_order(order["orderID"])
	assert len(pp_tracker.get_orders())  == 0

def test_add_resting_but_not_filling():
    order_rest_in_book = [{'price': 5000.5, 'orderQty': 100, 'side': "Buy"}]
    pp_tracker = paperless_tracker.getInstance()
    pp_tracker.reset()
    Test1Ei = Test1()
    pp_tracker.provide_exchange(Test1Ei)
    pp_tracker.track_orders_created(order_rest_in_book)
    #pp_tracker.loop_functions()
    pp_tracker.simulate_fills_from_trades()
    pp_tracker.close_positions()
    assert pp_tracker.filled == []

def test_getting_filled_after_several_loops():
    class Test2(ExchangeInterface):   
        def __init__(self):
            self.timestamp = iso8601.parse_date('2018-08-09T20:11:55.000Z')
            self.counter = 0
        def current_timestamp(self):
            return self.timestamp
        def market_depth(self, symbol):
            return orderbook
        def recent_trades(self):
            return trades 
        def updated_timestamp(self, timestamp):
            self.timestamp = timestamp
    order_rest_in_book = [{'price': 5000.5, 'orderQty': 100, 'side': "Buy"}]
    pp_tracker = paperless_tracker.getInstance()
    pp_tracker.reset()
    Test2Ei = Test2()
    pp_tracker.provide_exchange(Test2Ei)
    pp_tracker.track_orders_created(order_rest_in_book)
    counter = 0
    while counter < 12:
        counter += 1
        pp_tracker.simulate_fills_from_trades()
        pp_tracker.close_positions()
    assert pp_tracker.buy_partially_filled == []
    assert len(pp_tracker.filled) == 1


def test_buy_fills_with_multiple_orders():
    orders = [{'price': 5000.5, 'orderQty': 100, 'side': "Buy"},
                    {'price': 5000, 'orderQty': 100, 'side': "Buy"},
                   {'price': 4999.5, 'orderQty': 100, 'side': "Buy"}]
    trades = [{'timestamp': '2018-08-09T20:11:57.000Z',
        'symbol': 'XBTUSD',
        'side': 'Sell',
        'size': 1100,
        'price': 5000.5},
        {'timestamp': '2018-08-09T20:11:56.000Z',
        'symbol': 'XBTUSD',
        'side': 'Sell',
        'size': 1050,
        'price': 5000}]
    class Test3(ExchangeInterface):   
        def __init__(self):
            self.timestamp = iso8601.parse_date('2018-08-09T20:11:55.000Z')
        def current_timestamp(self):
            return self.timestamp
        def market_depth(self, symbol):
            return orderbook
        def recent_trades(self):
            return trades 
        def updated_timestamp(self, timestamp):
            self.timestamp = timestamp
    pp_tracker = paperless_tracker.getInstance()
    pp_tracker.reset()
    TestC = Test3()
    pp_tracker.provide_exchange(TestC)
    pp_tracker.track_orders_created(orders)
    pp_tracker.simulate_fills_from_trades()
    pp_tracker.close_positions()
    assert pp_tracker.position["currentQty"] == 150
    assert len(pp_tracker.get_orders()) == 2

def test_sell_fills_with_multiple_orders():
    orders = [{'price': 5001, 'orderQty': 100, 'side': "Sell"},
                    {'price': 5001.5, 'orderQty': 100, 'side': "Sell"},
                   {'price': 5002, 'orderQty': 100, 'side': "Sell"}]
    ltrades = [{'timestamp': '2018-08-09T20:11:57.000Z',
        'symbol': 'XBTUSD',
        'side': 'Buy',
        'size': 1100,
        'price': 5001},
        {'timestamp': '2018-08-09T20:11:56.000Z',
        'symbol': 'XBTUSD',
        'side': 'Buy',
        'size': 1050,
        'price': 5001.5}]
    class Test3(ExchangeInterface):   
        def __init__(self):
            self.timestamp = iso8601.parse_date('2018-08-09T20:11:55.000Z')
        def current_timestamp(self):
            return self.timestamp
        def market_depth(self, symbol):
            return orderbook
        def recent_trades(self):
            return ltrades
        def updated_timestamp(self, timestamp):
            self.timestamp = timestamp
    pp_tracker = paperless_tracker.getInstance()
    pp_tracker.reset()
    TestC = Test3()
    pp_tracker.provide_exchange(TestC)
    pp_tracker.track_orders_created(orders)
    pp_tracker.simulate_fills_from_trades()
    pp_tracker.close_positions()
    assert pp_tracker.position["currentQty"] == -150
    assert len(pp_tracker.get_orders()) == 2