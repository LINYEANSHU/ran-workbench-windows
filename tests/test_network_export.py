import csv
import json

import pytest

from ran_workbench.model import default_project
from ran_workbench.network import attach_network
from ran_workbench.project_io import export_result


def test_csv_contains_per_bs_sinr_noise_and_cooperation_without_changing_link_power(tmp_path):
    p = default_project()
    result = dict(mode='ue',scene_sha256='test',points=[],ues=[dict(ue_id='UE-01',
        position_m=[0,0,1.5],links=[dict(bs_id='BS-01',incoherent_dbm=-60,
        coherent_dbm=-60,wideband_dbm=-60,path_count=1,carrier_phasor=[1.,0.])])])
    attach_network(result,p)
    target=tmp_path/'network.csv'
    export_result(result,target)
    with target.open(encoding='utf-8-sig',newline='') as file:
        rows=list(csv.DictReader(file))
    assert len(rows)==1
    row=rows[0]
    assert float(row['incoherent_dbm'])==-60
    assert float(row['sinr_db'])==pytest.approx(result['ues'][0]['network']['sinr_db'])
    assert row['serving_bs_id']=='BS-01'
    assert float(row['noise_dbm']) < -90
    assert float(row['cooperation_gain_db'])==0
    assert row['channel']=='0'
    assert row['unresolved_interferers']==''
    assert float(row['total_received_dbm'])==-60
    assert row['requested_bs_count']=='1' and row['resolved_bs_count']=='1'
    export_result(result,tmp_path/'network.json')
    assert json.loads((tmp_path/'network.json').read_text(encoding='utf-8'))==result
