"""Offline-only integration tests. Synthetic values are test fixtures, NEVER displayed as live market data."""
import os, tempfile, sys
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from fastapi.testclient import TestClient
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
import v611_features as f, v611_auction_analysis as aa, app as server

def run():
    with tempfile.TemporaryDirectory() as tmp:
        db=Path(tmp)/'fixture.db';f.DB=db;aa.DB=db;server.DB_PATH=db
        f.init();aa.init();server.init_db()
        assert f.freeze_get('2026-09-23','next5') is None
        assert f.freeze_put('2026-09-23','next5',{'picks':[{'code':'600487'}]})
        assert f.freeze_put('2026-09-23','next5',{'picks':[{'code':'000001'}]})
        assert f.freeze_get('2026-09-23','next5')['picks'][0]['code']=='600487'
        print('PASS once-per-day frozen picks survive repeat requests (inside persistent DB)')
        candles=[]
        for i in range(45):
            candles.append({'date':f'day-{i}','open':10.,'close':10.,'high':10.2,'low':9.7,
              'volume':1000000,'amount':1e8,'turnover_rate':2.})
        candles[19].update(open=10.,close=11.,high=11.,amount=5.01e9)
        candles[25].update(open=10.,close=10.3,high=11.05)
        candles[31].update(open=10.,close=10.1,high=10.7)
        candles[43]['turnover_rate']=10.5
        candles[44].update(close=10.6,high=10.8)
        hit=f.technical_evaluate('600001','合规样本',pd.DataFrame(candles),float_cap=5e9)
        assert hit and all(hit['checks'].values()),hit
        assert f.technical_evaluate('688001','合规样本',pd.DataFrame(candles),float_cap=5e9) is None
        assert f.technical_evaluate('600001','*ST合规',pd.DataFrame(candles),float_cap=5e9) is None
        print('PASS all-eight technical AND requirements; 688 and ST excluded')
        with patch.object(f,'now',return_value=__import__('datetime').datetime(2026,9,23,10,0,tzinfo=f.CN)):
            assert not f.auction_capture().get('ok')
        print('PASS auction refuses to label intraday quote a genuine 09:25 snapshot')
        result=f.auction_view({'trade_date':'2026-09-23'})
        assert result['items']==[]
        assert aa.get_topics('2026-09-23')['topics']==[]
        bt=aa.replay_real_archive(days=30)
        assert bt['signal_count']==0
        print('PASS missing auction/sector/history data remain empty; no invented performance')
        with patch.object(f,'fetch_tencent_quotes',return_value=([{'code':'600487','name':'合规样本','price':20,'pct':2,'turnover_rate':9,'volume_ratio':1.8}],{'provider':'腾讯财经实时'})):
            f.watch_add('600487');a=f.watch_list()['items'][0]
            assert a['added_price']==20 and a['score'] is not None
            assert a['since_added_pct']==0
            print('PASS watchlist records actual supplied quote and explanatory research score')
        client=TestClient(server.app)
        r=client.get('/api/health');assert r.status_code==200 and r.json()['version']=='6.11.1-nonblocking-review'
        assert client.get('/api/auction25').json()['total']==0
        assert client.get('/api/auction25/topics').json()['topics']==[]
        assert client.get('/api/auction25/backtest').json()['signal_count']==0
        r=client.post('/api/watchlist',json={'code':'600001'})
        assert r.status_code==503
        print('PASS API version, empty genuine-data endpoints, owner protection')
if __name__=='__main__':run()
