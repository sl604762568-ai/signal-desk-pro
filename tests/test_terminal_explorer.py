"""Offline tests; all prices in this file are fixtures, never used by the website."""
import time
import unittest
from collections import deque
from unittest.mock import patch
import terminal_explorer as te


class RealOnlyTerminalTests(unittest.TestCase):
    def test_hot_group_uses_genuine_board_code(self):
        fixtures=[{'code':'BK1234','name':'存储芯片','pct':2.1,'breadth':75.,'main_net_pct':1.2,'main_net':6000000,'turnover':1.3},
                  {'code':'BK4321','name':'电力设备','pct':1.1,'breadth':50.,'main_net_pct':-.2,'main_net':-800000,'turnover':2.3}]
        with patch.object(te,'fetch_board_list',return_value=fixtures):
            result=te._fetch_category('hot','存储')
        self.assertEqual([b['code'] for b in result['boards']],['BK1234'])
        self.assertIsNotNone(result['boards'][0]['heat_score'])

    def test_minute_gap_does_not_fabricate_speed(self):
        with patch.object(te,'_trade_session',return_value=True):
            te._observed['600001']=deque([(time.time()-300,{'price':10,'amount':100})],maxlen=22)
            sig=te._signals('600001',{})
        self.assertIsNone(sig['speed_1m_pct'])

    def test_real_snapshot_time_window_math(self):
        now=time.time()
        h=[]; amount=0
        for i in range(11):
            amount += (i+1)*1000000
            h.append((now-(10-i)*60,{'price':10+i*.1,'amount':amount}))
        te._observed['600002']=deque(h,maxlen=22)
        with patch.object(te,'_trade_session',return_value=True):
            s=te._signals('600002',{})
        self.assertAlmostEqual(s['speed_1m_pct'],(11/10.9-1)*100,places=3)
        self.assertIsNotNone(s['amplitude_10m_pct'])
        self.assertTrue(s['volume_increasing_3'])

    def test_no_live_universe_means_no_invented_results(self):
        with patch.object(te,'observe',return_value={'rows':[],'symbols':0,'error':'源暂不可用'}):
            res=te.scan(['600001'],conditions={'min_price':1})
        self.assertEqual(res['matched'],0)
        self.assertEqual(res['rows'],[])

    def test_float_cap_filter_uses_verified_metadata(self):
        row={'code':'600001','name':'离线标的','price':12.,'pct':2.,'amount':110.,'volume_ratio':2.,'turnover_rate':8.}
        with patch.object(te,'observe',return_value={'rows':[row],'symbols':1,'provider':'离线fixture'}):
            with patch.object(te,'_trade_session',return_value=False):
                valid=te.scan(['600001'],conditions={'min_float_cap':10e8},metadata={'600001':{'float_market_cap':35e8}})
                invalid=te.scan(['600001'],conditions={'min_float_cap':40e8},metadata={'600001':{'float_market_cap':35e8}})
        self.assertEqual(valid['matched'],1)
        self.assertEqual(invalid['matched'],0)


if __name__=='__main__':unittest.main()
