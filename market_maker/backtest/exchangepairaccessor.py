import csv
import iso8601
import os
#from market_maker.backtest.timekeeper import Timekeeper
from decimal import Decimal


class ExchangePairAccessor(object):

    """ExchangePairAccessor."""

    def __init__(self, timekeeper = None, trades_filename = "", L2orderbook_filename = "",
                name = ""):
        """Init BacktestExchangePair."""
        # variables holding data dependent on current timestamp
        # timestamp tracking location of last read data line
        self.present_timestamp = None
        self.trades = []
        self.orderbook = None
        # timestamp tracking most recent timestamp that was requested to update up to
        self.external_timestamp = None
        
        # Using the '_' prefix to indicate persistent variables that have data  
        # that should not be directly shared outside of the class
        trade_data = []
        trades_csvfile = open(trades_filename)            
        tradereader = csv.reader(trades_csvfile, delimiter=';')
        self._timestamps = []
        unprocessed_trade_data = []
        for row in tradereader:
            unprocessed_trade_data.append(row)
        self._headers = unprocessed_trade_data.pop(0)
        self._trade_data = []
        #store the date prefix for the orderbook data
        self._date_prefix = unprocessed_trade_data[0][1][0:11]
        for row in unprocessed_trade_data:
            timestamp = iso8601.parse_date(row[1])
            self._timestamps.append(timestamp)
            # 'time_coinapi', 'price', 'base_amount', 'taker_side'
            completed_trade = {'timestamp': timestamp , 'guid': row[2], 'price': Decimal(row[3]), 
                               'base_amount': Decimal(row[4]), 'taker_side': row[5]}
            self._trade_data.append(completed_trade)
        
        # load orderbook data 
        self._orderbook_data = []
        self._orderbook_timestamps = []
        if L2orderbook_filename != "": 

            orderbook_csvfile = open(trades_filename)            
            orderbookreader = csv.reader(L2orderbook_filename, delimiter=';')
            unprocessed_orderbook_data = []
            for row in orderbookreader:
                unprocessed_orderbook_data.append(row)
            self._headers2 = unprocessed_orderbook_data.pop(0)
            for row in unprocessed_orderbook_data:
                timestamp = iso8601.parse_date(self._date_prefix+row[1])
                # let's not include orderbook timestamps for timekeeper
                self._orderbook_timestamps.append(timestamp)
                orderbook_snapshot = []
                for x in range(0,50):
                    ask_price = row[4*x + 2]
                    ask_size  = row[4*x + 3]
                    bid_price = row[4*x + 4]
                    bid_size  = row[4*x + 5]
                    if ask_price != "":
                        orderbook1 = { 'side': 'Sell',
                                       'size': Decimal(ask_size),
                                       'price': Decimal(ask_price)}
                        orderbook_snapshot.append(orderbook1)
                    if bid_price != "":
                        orderbook2 = { 'side': 'Buy',
                                       'size': Decimal(bid_size),
                                       'price': Decimal(ask_size)}
                        orderbook_snapshot.append(orderbook2)

                self._orderbook_data.append(orderbook_snapshot)

        # Contribute to timekeeper
        self._timekeeper = timekeeper
        self._timekeeper.contribute_times(self._timestamps)
        self._current_trades_location = 0
        self._current_orderbook_location = 0
        self.name = name
        
    #
    # Public methods
    #
    def current_timestamp(self):
        self._make_updates()
        return self.external_timestamp

    def wait_update(self):
        self._make_updates()
        pass
    
    def is_warm(self):
        self._make_updates()
        '''Checks to see if there is market data.'''
        return len(self.trades) > 0
        
    '''
    def ticker_data(self, symbol=None):
        """Get ticker data."""
        if symbol is None:
            symbol = self.symbol
        return self.ws.get_ticker(symbol)
    '''

    def market_depth(self):
        """Get market depth / orderbook."""
        self._make_updates()
        #fail if trades requested before warm
        self._fail_if_not_warm()

        return self.orderbook


    def recent_trades(self, number=50):
        """Get recent trades. Defaults to returning 50 trades. 

        Returns
        -------
        A list of dicts:
               {'timestamp': 
                   datetime.datetime(2018, 9, 1, 0, 0, 5, 302945, 
                   tzinfo=datetime.timezone.utc),
                'guid': 'd180ef47-1e99-455a-8e88-be6c0ccc4d6e', 
                'price': Decimal('7017), 
                'base_amount': Decimal('2000'), 
                'taker_side': 'SELL'} # or 'BUY'

        """
        
        self._make_updates()
        #fail if trades requested before warm
        self._fail_if_not_warm()
        number_to_return = min(len(self.trades), number)
        return self.trades[-number_to_return:]
    
    #
    # Private methods
    #
        
    def _update_to_timestamp(self, timestamp):
        next_trade = self._trade_data[self._current_trades_location]
        while next_trade['timestamp'] <= timestamp:
            #Apply Trade Data
            self.trades.append(next_trade)      
            self._current_trades_location += 1
            if self._current_trades_location == len(self._trade_data):
                raise EOFError()
            self.present_timestamp = next_trade['timestamp']
            next_trade = self._trade_data[self._current_trades_location]
        
    def update_orderbook(self, timestamp):
        next_orderbook = self._orderbook_timestamps[self._current_orderbook_location]
        while next_orderbook <= timestamp:
            if self._current_orderbook_location + 1 == len(self._orderbook_data):
                break
            self._current_orderbook_location += 1
        self.orderbook = self._orderbook_data[self._current_orderbook_location]


    def _make_updates(self):
        to_timestamp = self._timekeeper.get_time()
        if self.present_timestamp is None or self.present_timestamp < to_timestamp:
            self._update_to_timestamp(to_timestamp)
        if self._orderbook_data:
            self.update_orderbook(to_timestamp)
        self.external_timestamp = to_timestamp
        # _update_to_timestamp should not have put the current_timestamp 
        # ahead of the timekeeper
        
    
    def _fail_if_not_warm(self):
        # use to ensure data is not requested before it is created
        to_timestamp = self._timekeeper.get_time()
        if self.trades == []:
            raise Exception("Accessing trades before class is warm!")
        
        #also check that present_timestamp doesn't exceed timekeeper
        assert self.present_timestamp <= to_timestamp
