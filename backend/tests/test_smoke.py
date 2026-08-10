import os
from datetime import timedelta
from pathlib import Path

TEST_DB = Path(__file__).resolve().parent.parent / "test_cps.db"
if TEST_DB.exists():
    TEST_DB.unlink()

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "ChangeMe123!"

from fastapi.testclient import TestClient
from app.main import app, business_today


def login(c, username, password):
    r = c.post('/api/auth/login', json={'username': username, 'password': password})
    assert r.status_code == 200, r.text
    return r.json()['access_token']


def auth(token):
    return {'Authorization': f'Bearer {token}'}


def create_agent(c, token, username, name, level, limit, commission=0.1):
    return c.post('/api/agents', headers=auth(token), json={
        'username': username,
        'password': 'AgentPass123!',
        'agent_name': name,
        'agent_level': level,
        'subagent_limit': limit,
        'commission_rate': commission,
    })


def test_login_and_dashboard():
    with TestClient(app) as c:
        token = login(c, 'admin', 'ChangeMe123!')
        r = c.get('/api/dashboard', headers=auth(token))
        assert r.status_code == 200
        dashboard = r.json()
        assert dashboard['dashboard_type'] == 'superadmin'
        for key in [
            'total_registrations', 'yesterday_registrations', 'today_registrations',
            'total_turnover', 'yesterday_turnover', 'today_turnover',
            'pending_abnormal', 'redeemed_cdk',
        ]:
            assert key in dashboard
        for key in ['commission_rate', 'yesterday_commission', 'today_commission', 'total_commission']:
            assert key not in dashboard
        me = c.get('/api/auth/me', headers=auth(token))
        assert me.status_code == 200
        assert me.json()['actor_type'] == 'admin'
        assert me.json()['role'] == 'superadmin'
        assert 'system.metrics' in me.json()['permissions']
        metrics = c.get('/api/system/metrics', headers=auth(token))
        assert metrics.status_code == 200, metrics.text
        metric_data = metrics.json()
        for key in ['cpu_percent', 'memory_percent', 'disk_percent', 'updated_at']:
            assert key in metric_data
        for key in ['cpu_percent', 'memory_percent', 'disk_percent']:
            if metric_data[key] is not None:
                assert 0 <= metric_data[key] <= 100
        caps = c.get('/api/agents/capabilities', headers=auth(token))
        assert caps.status_code == 200
        assert caps.json()['current_level_name'] == '超级管理员'
        assert caps.json()['allowed_child_level'] == 1


def test_agent_id_invite_parent_level_and_limit_are_controlled():
    with TestClient(app) as c:
        admin_token = login(c, 'admin', 'ChangeMe123!')

        parent = create_agent(c, admin_token, 'parent_agent', '一级代理', 1, 2)
        assert parent.status_code == 200, parent.text
        parent_data = parent.json()
        assert parent_data['agent_id'].startswith('A')
        assert parent_data['agent_id'][1:].isdigit()
        assert parent_data['invite_code'] == parent_data['agent_id']
        assert parent_data['parent_agent_id'] is None
        assert parent_data['parent_agent_display'] == '超管'
        assert parent_data['agent_level'] == 1
        assert parent_data['subagent_limit'] == 2

        parent_token = login(c, 'parent_agent', 'AgentPass123!')
        child = create_agent(c, parent_token, 'child_agent', '二级代理', 2, 1, 0.05)
        assert child.status_code == 200, child.text
        child_data = child.json()
        assert child_data['agent_id'].startswith('A')
        assert child_data['agent_id'][1:].isdigit()
        assert child_data['agent_id'] != parent_data['agent_id']
        assert child_data['parent_agent_id'] == parent_data['agent_id']
        assert child_data['agent_level'] == 2
        assert child_data['subagent_limit'] == 1
        assert child_data['invite_code'] == child_data['agent_id']
        assert child_data['invite_code'] != parent_data['invite_code']

        rows = c.get('/api/agents', headers=auth(parent_token))
        assert rows.status_code == 200, rows.text
        row = next(x for x in rows.json() if x['username'] == 'child_agent')
        assert row['agent_level_name'] == '二级代理'
        assert row['subagent_limit'] == 1
        assert row['subagent_count'] == 0
        assert 'subagent_remaining' not in row


def test_three_level_chain_and_creation_permissions():
    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')

        wrong = create_agent(c, admin, 'wrong_level', '越级代理', 2, 1)
        assert wrong.status_code == 403
        assert '只能开通一级代理' in wrong.json()['detail']

        l1 = create_agent(c, admin, 'level1_agent', '一级A', 1, 1)
        assert l1.status_code == 200, l1.text
        l1_token = login(c, 'level1_agent', 'AgentPass123!')

        l2 = create_agent(c, l1_token, 'level2_agent', '二级A', 2, 1)
        assert l2.status_code == 200, l2.text

        quota_full = create_agent(c, l1_token, 'level2_agent_extra', '二级B', 2, 1)
        assert quota_full.status_code == 403
        assert '名额已用完' in quota_full.json()['detail']

        l2_token = login(c, 'level2_agent', 'AgentPass123!')
        bad_l3_limit = create_agent(c, l2_token, 'level3_bad', '三级错误额度', 3, 5)
        assert bad_l3_limit.status_code == 400

        l3 = create_agent(c, l2_token, 'level3_agent', '三级A', 3, 0)
        assert l3.status_code == 200, l3.text
        assert l3.json()['agent_level'] == 3
        assert l3.json()['subagent_limit'] == 0

        l3_token = login(c, 'level3_agent', 'AgentPass123!')
        # 三级代理后台不显示渠道管理，因此渠道能力接口也不可访问。
        cap = c.get('/api/agents/capabilities', headers=auth(l3_token))
        assert cap.status_code == 403

        forbidden = create_agent(c, l3_token, 'level4_agent', '不存在的四级', 3, 0)
        assert forbidden.status_code == 403
        assert '无此操作权限' in forbidden.json()['detail']


def test_player_registers_via_agent_link_and_is_auto_bound():
    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        l1 = create_agent(c, admin, 'register_link_agent', '注册链接代理', 1, 2)
        assert l1.status_code == 200, l1.text
        agent_public_id = l1.json()['agent_id']

        info = c.get(f'/api/public/registration/{agent_public_id}')
        assert info.status_code == 200, info.text
        assert info.json()['agent_id'] == agent_public_id

        reg = c.post(f'/api/public/registration/{agent_public_id}', json={
            'username': 'linked_player_001',
            'password': 'PlayerPass123!',
        })
        assert reg.status_code == 200, reg.text
        data = reg.json()
        assert data['player_id'].startswith('P')
        assert data['agent_id'] == agent_public_id

        agent_token = login(c, 'register_link_agent', 'AgentPass123!')
        me = c.get('/api/auth/me', headers=auth(agent_token))
        assert me.status_code == 200
        assert me.json()['registration_path'] == f'/register/{agent_public_id}'

        players = c.get('/api/players', headers=auth(agent_token))
        assert players.status_code == 200, players.text
        row = next(x for x in players.json() if x['username'] == 'linked_player_001')
        assert row['agent_public_id'] == agent_public_id
        assert row['primary_role_name'] == '未绑定'
        assert row['characters'] == []

        # 后台不再允许手工新增玩家，统一从代理专属注册地址进入。
        manual = c.post('/api/players', headers=auth(admin), json={
            'username': 'manual_player', 'password': 'PlayerPass123!'
        })
        assert manual.status_code == 405


def test_commission_rate_is_internal_ratio_and_validated():
    with TestClient(app) as c:
        token = login(c, 'admin', 'ChangeMe123!')
        ok = create_agent(c, token, 'percent_agent', '百分比代理', 1, 10, 0.5)
        assert ok.status_code == 200, ok.text
        rows = c.get('/api/agents', headers=auth(token))
        assert rows.status_code == 200
        row = next(x for x in rows.json() if x['username'] == 'percent_agent')
        assert row['commission_rate'] == 0.5

        bad = c.post('/api/agents', headers=auth(token), json={
            'username': 'bad_percent_agent',
            'password': 'PercentPass123!',
            'agent_name': '错误比例代理',
            'agent_level': 1,
            'subagent_limit': 10,
            'commission_rate': 50,
        })
        assert bad.status_code == 422


def test_agent_query_bar_filters_and_custom_turnover():
    from datetime import date
    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        l1 = create_agent(c, admin, 'query_parent', '查询上级', 1, 3, 0.2)
        assert l1.status_code == 200, l1.text
        parent_public_id = l1.json()['agent_id']
        l1_token = login(c, 'query_parent', 'AgentPass123!')
        l2 = create_agent(c, l1_token, 'query_child', '查询下级', 2, 2, 0.1)
        assert l2.status_code == 200, l2.text
        child_public_id = l2.json()['agent_id']

        # 超管可查询全部等级，并可按账号、代理ID、上级代理查询。
        by_account = c.get('/api/agents', headers=auth(admin), params={'agent_account': 'query_child'})
        assert by_account.status_code == 200, by_account.text
        assert any(x['agent_id'] == child_public_id for x in by_account.json())

        by_id = c.get('/api/agents', headers=auth(admin), params={'public_agent_id': child_public_id})
        assert by_id.status_code == 200, by_id.text
        assert len(by_id.json()) == 1

        by_parent = c.get('/api/agents', headers=auth(admin), params={'parent': parent_public_id})
        assert by_parent.status_code == 200, by_parent.text
        assert any(x['agent_id'] == child_public_id for x in by_parent.json())

        by_superadmin = c.get('/api/agents', headers=auth(admin), params={'parent': '超管'})
        assert by_superadmin.status_code == 200, by_superadmin.text
        assert any(x['agent_id'] == parent_public_id and x['parent_agent_display'] == '超管' for x in by_superadmin.json())

        child_row = next(x for x in by_account.json() if x['agent_id'] == child_public_id)
        player = c.post(f'/api/public/registration/{child_public_id}', json={
            'username': 'query_player',
            'password': 'PlayerPass123!',
        })
        assert player.status_code == 200, player.text
        assert player.json()['agent_id'] == child_public_id
        player_pk = player.json()['id']
        order = c.post('/api/orders/platform', headers=auth(admin), json={
            'order_no': 'QUERYORDER001',
            'player_id': player_pk,
            'agent_id': child_row['id'],
            'amount': 88.66,
            'platform_coin': 8866,
            'payment_channel': 'manual',
            'pay_status': 'paid',
        })
        assert order.status_code == 200, order.text

        # 今日/昨日/自定义是 V12 的三种流水查询模式。
        today_period = c.get('/api/agents', headers=auth(admin), params={
            'public_agent_id': child_public_id,
            'turnover_period': 'today',
        })
        assert today_period.status_code == 200, today_period.text
        assert today_period.json()[0]['period_turnover'] == 88.66
        assert today_period.json()[0]['turnover_period'] == 'today'

        yesterday_period = c.get('/api/agents', headers=auth(admin), params={
            'public_agent_id': child_public_id,
            'turnover_period': 'yesterday',
        })
        assert yesterday_period.status_code == 200, yesterday_period.text
        assert yesterday_period.json()[0]['period_turnover'] == 0

        today = str(business_today())
        custom_period = c.get('/api/agents', headers=auth(admin), params={
            'public_agent_id': child_public_id,
            'turnover_period': 'custom',
            'turnover_start': today,
            'turnover_end': today,
        })
        assert custom_period.status_code == 200, custom_period.text
        assert custom_period.json()[0]['period_turnover'] == 88.66

        missing_custom_range = c.get('/api/agents', headers=auth(admin), params={
            'turnover_period': 'custom',
        })
        assert missing_custom_range.status_code == 400

        bad_range = c.get('/api/agents', headers=auth(admin), params={
            'turnover_period': 'custom',
            'turnover_start': '2026-08-10',
            'turnover_end': '2026-08-09',
        })
        assert bad_range.status_code == 400


def test_agent_edit_password_ban_commission_limit_and_reparent():
    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')

        l1a = create_agent(c, admin, 'edit_l1a', '编辑一级A', 1, 3, 0.10)
        l1b = create_agent(c, admin, 'edit_l1b', '编辑一级B', 1, 3, 0.12)
        assert l1a.status_code == 200 and l1b.status_code == 200
        l1a_data, l1b_data = l1a.json(), l1b.json()
        l1a_token = login(c, 'edit_l1a', 'AgentPass123!')

        l2 = create_agent(c, l1a_token, 'edit_l2', '编辑二级', 2, 2, 0.15)
        assert l2.status_code == 200, l2.text
        l2_data = l2.json()

        rows = c.get('/api/agents', headers=auth(admin), params={'public_agent_id': l2_data['agent_id']}).json()
        target = rows[0]

        options = c.get(f"/api/agents/{target['id']}/edit-options", headers=auth(admin))
        assert options.status_code == 200, options.text
        opt_data = options.json()
        assert opt_data['can_change_parent'] is True
        assert any(x['value'] == l1b_data['agent_id'] for x in opt_data['parent_options'])

        updated = c.patch(f"/api/agents/{target['id']}", headers=auth(admin), json={
            'password': 'NewAgentPass456!',
            'status': 'active',
            'commission_rate': 0.35,
            'subagent_limit': 5,
            'parent_agent_id': l1b_data['agent_id'],
        })
        assert updated.status_code == 200, updated.text
        changed = updated.json()['agent']
        assert changed['parent_agent_id'] == l1b_data['agent_id']
        assert changed['commission_rate'] == 0.35
        assert changed['subagent_limit'] == 5

        # 密码已经真正更新。
        assert c.post('/api/auth/login', json={'username': 'edit_l2', 'password': 'AgentPass123!'}).status_code == 401
        assert c.post('/api/auth/login', json={'username': 'edit_l2', 'password': 'NewAgentPass456!'}).status_code == 200

        # 原上级已无权编辑，新的直属上级可编辑，但不能自行更改归属。
        old_parent_edit = c.patch(f"/api/agents/{target['id']}", headers=auth(l1a_token), json={'commission_rate': 0.2})
        assert old_parent_edit.status_code == 403
        l1b_token = login(c, 'edit_l1b', 'AgentPass123!')
        child_options = c.get(f"/api/agents/{target['id']}/edit-options", headers=auth(l1b_token))
        assert child_options.status_code == 200
        child_opt = child_options.json()
        assert child_opt['can_change_parent'] is False
        assert child_opt['can_full_edit'] is False
        assert child_opt['editable_fields'] == ['agent_name', 'commission_rate']

        # 普通代理只能修改直属下级的代理名称与佣金比例。
        limited_edit = c.patch(f"/api/agents/{target['id']}", headers=auth(l1b_token), json={
            'agent_name': '新的二级代理名称',
            'commission_rate': 0.22,
        })
        assert limited_edit.status_code == 200, limited_edit.text
        limited_data = limited_edit.json()['agent']
        assert limited_data['agent_name'] == '新的二级代理名称'
        assert limited_data['commission_rate'] == 0.22

        for forbidden_payload in [
            {'parent_agent_id': l1a_data['agent_id']},
            {'status': 'disabled'},
            {'subagent_limit': 9},
            {'password': 'AgentForbidden999!'},
        ]:
            denied = c.patch(f"/api/agents/{target['id']}", headers=auth(l1b_token), json=forbidden_payload)
            assert denied.status_code == 403, (forbidden_payload, denied.text)
            assert '只能修改直属下级的代理名称和佣金比例' in denied.json()['detail']

        # 封禁后该代理后台立即不可登录；超管可以再解封。
        banned = c.patch(f"/api/agents/{target['id']}", headers=auth(admin), json={'status': 'disabled'})
        assert banned.status_code == 200
        assert c.post('/api/auth/login', json={'username': 'edit_l2', 'password': 'NewAgentPass456!'}).status_code == 401
        unbanned = c.patch(f"/api/agents/{target['id']}", headers=auth(admin), json={'status': 'active'})
        assert unbanned.status_code == 200
        assert c.post('/api/auth/login', json={'username': 'edit_l2', 'password': 'NewAgentPass456!'}).status_code == 200


def test_edit_limit_cannot_be_below_opened_children_and_hierarchy_is_strict():
    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        l1 = create_agent(c, admin, 'limit_edit_l1', '限额一级', 1, 3)
        assert l1.status_code == 200
        l1_token = login(c, 'limit_edit_l1', 'AgentPass123!')
        l2 = create_agent(c, l1_token, 'limit_edit_l2', '限额二级', 2, 2)
        assert l2.status_code == 200
        l2_token = login(c, 'limit_edit_l2', 'AgentPass123!')
        l3 = create_agent(c, l2_token, 'limit_edit_l3', '限额三级', 3, 0)
        assert l3.status_code == 200

        l2_row = c.get('/api/agents', headers=auth(admin), params={'public_agent_id': l2.json()['agent_id']}).json()[0]
        too_low = c.patch(f"/api/agents/{l2_row['id']}", headers=auth(admin), json={'subagent_limit': 0})
        assert too_low.status_code == 400
        assert '不能小于已开通数量' in too_low.json()['detail']

        # 二级代理不能直接改为归属超管。
        bad_parent = c.patch(f"/api/agents/{l2_row['id']}", headers=auth(admin), json={'parent_agent_id': 'SUPERADMIN'})
        assert bad_parent.status_code == 400
        assert '必须归属一级代理' in bad_parent.json()['detail']


def test_unified_login_rbac_and_scoped_agent_views():
    with TestClient(app) as c:
        admin_login = c.post('/api/auth/login', json={'username': 'admin', 'password': 'ChangeMe123!'})
        assert admin_login.status_code == 200
        admin_data = admin_login.json(); admin = admin_data['access_token']
        assert admin_data['actor_type'] == 'admin'
        assert 'mail.send' in admin_data['permissions']
        assert 'products.manage' in admin_data['permissions']

        l1 = create_agent(c, admin, 'rbac_l1', 'RBAC一级', 1, 2, 0.2)
        assert l1.status_code == 200, l1.text
        l1_login = c.post('/api/auth/login', json={'username': 'rbac_l1', 'password': 'AgentPass123!'})
        assert l1_login.status_code == 200
        l1_data = l1_login.json(); l1_token = l1_data['access_token']
        assert l1_data['agent_level'] == 1
        assert 'dashboard.view' in l1_data['permissions']
        assert 'channels.view' in l1_data['permissions']
        assert 'channels.create' in l1_data['permissions']
        assert 'settlements.view' in l1_data['permissions']
        assert 'players.view' in l1_data['permissions']
        assert 'orders.view' in l1_data['permissions']
        assert 'shipments.view' in l1_data['permissions']
        for forbidden_permission in ['products.view', 'cdk.view', 'recharge.view', 'claims.view', 'mail.view', 'mail.send']:
            assert forbidden_permission not in l1_data['permissions']

        l2 = create_agent(c, l1_token, 'rbac_l2', 'RBAC二级', 2, 1, 0.1)
        assert l2.status_code == 200, l2.text
        l2_login = c.post('/api/auth/login', json={'username': 'rbac_l2', 'password': 'AgentPass123!'})
        assert l2_login.status_code == 200
        l2_data = l2_login.json(); l2_token = l2_data['access_token']
        assert l2_data['agent_level'] == 2
        assert 'channels.view' in l2_data['permissions']
        assert 'channels.create' in l2_data['permissions']
        assert 'players.view' in l2_data['permissions']
        assert 'orders.view' in l2_data['permissions']
        assert 'dashboard.view' in l2_data['permissions']
        assert 'products.view' not in l2_data['permissions']
        assert 'system.metrics' not in l2_data['permissions']
        assert c.get('/api/system/metrics', headers=auth(l2_token)).status_code == 403

        l3 = create_agent(c, l2_token, 'rbac_l3', 'RBAC三级', 3, 0, 0.05)
        assert l3.status_code == 200, l3.text
        l3_login = c.post('/api/auth/login', json={'username': 'rbac_l3', 'password': 'AgentPass123!'})
        assert l3_login.status_code == 200
        l3_data = l3_login.json(); l3_token = l3_data['access_token']
        assert l3_data['agent_level'] == 3
        assert 'channels.view' not in l3_data['permissions']
        assert 'channels.create' not in l3_data['permissions']
        assert 'channels.edit_basic' not in l3_data['permissions']
        assert 'settlements.view' not in l3_data['permissions']
        assert 'players.view' in l3_data['permissions']
        assert 'orders.view' in l3_data['permissions']
        assert 'shipments.view' in l3_data['permissions']
        assert 'dashboard.view' in l3_data['permissions']

        # 一级/二级代理拥有数据总览、渠道、玩家、订单；其它系统接口仅超管可访问。
        assert c.get('/api/agents', headers=auth(l1_token)).status_code == 200
        assert c.get('/api/players', headers=auth(l1_token)).status_code == 200
        assert c.get('/api/orders/platform', headers=auth(l1_token)).status_code == 200
        l1_dashboard = c.get('/api/dashboard', headers=auth(l1_token))
        assert l1_dashboard.status_code == 200
        l1_dashboard_data = l1_dashboard.json()
        assert l1_dashboard_data['dashboard_type'] == 'agent'
        for key in ['commission_rate', 'yesterday_commission', 'today_commission', 'total_commission']:
            assert key in l1_dashboard_data
        assert 'pending_abnormal' not in l1_dashboard_data
        assert 'redeemed_cdk' not in l1_dashboard_data
        assert c.get('/api/products', headers=auth(l1_token)).status_code == 403
        assert c.get('/api/redemption-batches', headers=auth(l1_token)).status_code == 403
        assert c.get('/api/recharge-rules', headers=auth(l1_token)).status_code == 403
        assert c.get('/api/claims', headers=auth(l1_token)).status_code == 403
        assert c.get('/api/mails', headers=auth(l1_token)).status_code == 403

        # 三级代理保留数据总览、玩家、订单；渠道管理接口与新增代理接口都被后端拒绝。
        assert c.get('/api/dashboard', headers=auth(l3_token)).status_code == 200
        assert c.get('/api/players', headers=auth(l3_token)).status_code == 200
        assert c.get('/api/orders/platform', headers=auth(l3_token)).status_code == 200
        assert c.get('/api/shipments', headers=auth(l3_token)).status_code == 200
        assert c.get('/api/agents', headers=auth(l3_token)).status_code == 403
        caps = c.get('/api/agents/capabilities', headers=auth(l3_token))
        assert caps.status_code == 403
        denied_create = create_agent(c, l3_token, 'rbac_l4', '禁止新增', 3, 0)
        assert denied_create.status_code == 403


def test_dashboard_turnover_counts_only_paid_platform_coin_orders():
    """V23: 数据总览流水排除商城订单和未支付平台币订单。"""
    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        before = c.get('/api/dashboard', headers=auth(admin))
        assert before.status_code == 200, before.text
        before_data = before.json()

        reg_agent = create_agent(c, admin, 'v23_flow_agent', 'V23流水代理', 1, 5, 0.1)
        assert reg_agent.status_code == 200, reg_agent.text
        reg_agent_id = reg_agent.json()['agent_id']
        player = c.post(f'/api/public/registration/{reg_agent_id}', json={
            'username': 'v23_flow_player',
            'password': 'PlayerPass123!',
        })
        assert player.status_code == 200, player.text
        player_pk = player.json()['id']

        product = c.post('/api/products', headers=auth(admin), json={
            'sku': 'V23-FLOW-PRODUCT-001',
            'name': 'V23流水测试商品',
            'category': 'product',
            'price': 67.89,
            'stock': 10,
            'description': 'dashboard turnover scope test',
        })
        assert product.status_code == 200, product.text
        product_pk = product.json()['id']

        paid_platform = c.post('/api/orders/platform', headers=auth(admin), json={
            'order_no': 'V23-PLATFORM-PAID-001',
            'player_id': player_pk,
            'amount': 123.45,
            'platform_coin': 12345,
            'payment_channel': 'manual',
            'pay_status': 'paid',
        })
        assert paid_platform.status_code == 200, paid_platform.text

        pending_platform = c.post('/api/orders/platform', headers=auth(admin), json={
            'order_no': 'V23-PLATFORM-PENDING-001',
            'player_id': player_pk,
            'amount': 999.00,
            'platform_coin': 99900,
            'payment_channel': 'manual',
            'pay_status': 'pending',
        })
        assert pending_platform.status_code == 200, pending_platform.text

        paid_mall = c.post('/api/orders/mall', headers=auth(admin), json={
            'order_no': 'V23-MALL-PAID-001',
            'player_id': player_pk,
            'product_id': product_pk,
            'quantity': 1,
            'amount': 67.89,
            'pay_status': 'paid',
        })
        assert paid_mall.status_code == 200, paid_mall.text

        after = c.get('/api/dashboard', headers=auth(admin))
        assert after.status_code == 200, after.text
        after_data = after.json()

        assert round(after_data['total_turnover'] - before_data['total_turnover'], 2) == 123.45
        assert round(after_data['today_turnover'] - before_data['today_turnover'], 2) == 123.45


def test_v29_superadmin_and_agent_registration_links():
    """V29: 超管与代理共用注册链路，超管直属玩家不绑定普通代理。"""
    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        me = c.get('/api/auth/me', headers=auth(admin))
        assert me.status_code == 200, me.text
        assert me.json()['registration_path'] == '/register/SUPERADMIN'

        info = c.get('/api/public/registration/SUPERADMIN')
        assert info.status_code == 200, info.text
        assert info.json()['channel_type'] == 'superadmin'
        assert info.json()['agent_id'] == '超管'

        direct = c.post('/api/public/registration/SUPERADMIN', json={
            'username': 'v29_super_direct_player',
            'password': 'PlayerPass123!',
        })
        assert direct.status_code == 200, direct.text
        assert direct.json()['channel_type'] == 'superadmin'
        assert direct.json()['agent_id'] == '超管'

        players = c.get('/api/players', headers=auth(admin))
        assert players.status_code == 200, players.text
        row = next(x for x in players.json() if x['username'] == 'v29_super_direct_player')
        assert row['agent_id'] is None
        assert row['agent_public_id'] == '超管'

        agent_resp = create_agent(c, admin, 'v29_link_agent', 'V29注册链接代理', 1, 2, 0.2)
        assert agent_resp.status_code == 200, agent_resp.text
        agent_id = agent_resp.json()['agent_id']
        agent_token = login(c, 'v29_link_agent', 'AgentPass123!')
        agent_me = c.get('/api/auth/me', headers=auth(agent_token))
        assert agent_me.status_code == 200
        assert agent_me.json()['registration_path'] == f'/register/{agent_id}'


def test_player_queries_and_superadmin_only_edit_controls():
    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        l1 = create_agent(c, admin, 'player_owner_agent', '玩家归属代理', 1, 3)
        assert l1.status_code == 200, l1.text
        agent_public_id = l1.json()['agent_id']
        agent_pk = l1.json()['id']
        agent_token = login(c, 'player_owner_agent', 'AgentPass123!')

        reg = c.post(f'/api/public/registration/{agent_public_id}', json={
            'username': 'editable_player_001',
            'password': 'PlayerPass123!',
        })
        assert reg.status_code == 200, reg.text
        player_pk = reg.json()['id']

        # 玩家列表查询：账号查询 + 上级代理查询。
        by_account = c.get('/api/players', headers=auth(admin), params={'account': 'editable_player'})
        assert by_account.status_code == 200, by_account.text
        assert any(x['id'] == player_pk for x in by_account.json())

        by_parent = c.get('/api/players', headers=auth(admin), params={'parent': agent_public_id})
        assert by_parent.status_code == 200, by_parent.text
        assert any(x['id'] == player_pk for x in by_parent.json())

        by_parent_account = c.get('/api/players', headers=auth(admin), params={'parent': 'player_owner_agent'})
        assert by_parent_account.status_code == 200, by_parent_account.text
        assert any(x['id'] == player_pk for x in by_parent_account.json())

        # 普通代理只能查看，不能修改玩家任何信息，也不能获取编辑资料。
        forbidden_info = c.get(f'/api/players/{player_pk}/edit', headers=auth(agent_token))
        assert forbidden_info.status_code == 403
        forbidden_patch = c.patch(f'/api/players/{player_pk}', headers=auth(agent_token), json={
            'status': 'disabled',
        })
        assert forbidden_patch.status_code == 403

        # 超管可封禁、修改归属、修改密码、发放/收回平台币。
        info = c.get(f'/api/players/{player_pk}/edit', headers=auth(admin))
        assert info.status_code == 200, info.text
        assert info.json()['owner_agent_id'] == agent_public_id
        assert info.json()['platform_coin_balance'] == 0

        issue = c.patch(f'/api/players/{player_pk}', headers=auth(admin), json={
            'password': 'NewPlayerPass123!',
            'status': 'disabled',
            'owner_agent_id': 'SUPERADMIN',
            'coin_action': 'issue',
            'coin_amount': 500,
        })
        assert issue.status_code == 200, issue.text
        assert issue.json()['platform_coin_balance'] == 500
        assert issue.json()['status'] == 'disabled'

        direct = c.get('/api/players', headers=auth(admin), params={'parent': '超管', 'account': 'editable_player'})
        assert direct.status_code == 200, direct.text
        row = next(x for x in direct.json() if x['id'] == player_pk)
        assert row['agent_public_id'] == '超管'
        assert row['platform_coin_balance'] == 500
        assert row['status'] == 'disabled'

        reclaim = c.patch(f'/api/players/{player_pk}', headers=auth(admin), json={
            'status': 'active',
            'owner_agent_id': agent_public_id,
            'coin_action': 'reclaim',
            'coin_amount': 200,
        })
        assert reclaim.status_code == 200, reclaim.text
        assert reclaim.json()['platform_coin_balance'] == 300

        too_much = c.patch(f'/api/players/{player_pk}', headers=auth(admin), json={
            'coin_action': 'reclaim',
            'coin_amount': 999,
        })
        assert too_much.status_code == 400
        assert '余额不足' in too_much.json()['detail']

        # 已支付平台币订单会真实增加玩家平台币余额。
        paid = c.post('/api/orders/platform', headers=auth(admin), json={
            'order_no': 'PLAYERCOINORDER001',
            'player_id': player_pk,
            'agent_id': agent_pk,
            'amount': 10,
            'platform_coin': 100,
            'payment_channel': 'manual',
            'pay_status': 'paid',
        })
        assert paid.status_code == 200, paid.text
        after_order = c.get('/api/players', headers=auth(admin), params={'account': 'editable_player'})
        row = next(x for x in after_order.json() if x['id'] == player_pk)
        assert row['platform_coin_balance'] == 400


def test_v31_all_turnover_uses_real_paid_platform_orders_only():
    """V31: 手工发币与商城订单都不能进入任何流水/分佣口径。"""
    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        agent_resp = create_agent(c, admin, 'v31_turnover_agent', 'V31真实流水代理', 1, 3, 0.25)
        assert agent_resp.status_code == 200, agent_resp.text
        agent = agent_resp.json()
        agent_pk = agent['id']
        agent_id = agent['agent_id']

        reg = c.post(f'/api/public/registration/{agent_id}', json={
            'username': 'v31_turnover_player',
            'password': 'PlayerPass123!',
        })
        assert reg.status_code == 200, reg.text
        player_pk = reg.json()['id']

        before = c.get('/api/dashboard', headers=auth(admin)).json()

        # 超管手工发放平台币只改余额，不得增加充值、流水或分佣。
        issue = c.patch(f'/api/players/{player_pk}', headers=auth(admin), json={
            'coin_action': 'issue',
            'coin_amount': 5000,
        })
        assert issue.status_code == 200, issue.text
        after_issue = c.get('/api/dashboard', headers=auth(admin)).json()
        assert after_issue['total_turnover'] == before['total_turnover']
        assert after_issue['today_turnover'] == before['today_turnover']

        player_row = next(x for x in c.get('/api/players', headers=auth(admin), params={'account': 'v31_turnover_player'}).json() if x['id'] == player_pk)
        assert player_row['platform_coin_balance'] == 5000
        assert player_row['total_recharge'] == 0
        assert player_row['today_recharge'] == 0

        agent_row = c.get('/api/agents', headers=auth(admin), params={'public_agent_id': agent_id}).json()[0]
        assert agent_row['total_turnover'] == 0
        assert agent_row['today_turnover'] == 0

        # 已支付商城订单也不属于平台币真实充值流水。
        product = c.post('/api/products', headers=auth(admin), json={
            'sku': 'V31-TURNOVER-PRODUCT-001',
            'name': 'V31流水隔离商品',
            'category': 'product',
            'price': 66.66,
            'stock': 5,
            'description': 'mall should not count as turnover',
        })
        assert product.status_code == 200, product.text
        mall = c.post('/api/orders/mall', headers=auth(admin), json={
            'order_no': 'V31-MALL-PAID-001',
            'player_id': player_pk,
            'agent_id': agent_pk,
            'product_id': product.json()['id'],
            'quantity': 1,
            'amount': 66.66,
            'pay_status': 'paid',
        })
        assert mall.status_code == 200, mall.text

        after_mall = c.get('/api/dashboard', headers=auth(admin)).json()
        assert after_mall['total_turnover'] == before['total_turnover']
        player_row = next(x for x in c.get('/api/players', headers=auth(admin), params={'account': 'v31_turnover_player'}).json() if x['id'] == player_pk)
        assert player_row['total_recharge'] == 0

        # 只有真实已支付的平台币订单进入流水。
        paid = c.post('/api/orders/platform', headers=auth(admin), json={
            'order_no': 'V31-PLATFORM-PAID-001',
            'player_id': player_pk,
            'agent_id': agent_pk,
            'amount': 120.50,
            'platform_coin': 12050,
            'payment_channel': 'alipay',
            'pay_status': 'paid',
        })
        assert paid.status_code == 200, paid.text

        final_dashboard = c.get('/api/dashboard', headers=auth(admin)).json()
        assert round(final_dashboard['total_turnover'] - before['total_turnover'], 2) == 120.50
        assert round(final_dashboard['today_turnover'] - before['today_turnover'], 2) == 120.50

        agent_row = c.get('/api/agents', headers=auth(admin), params={'public_agent_id': agent_id}).json()[0]
        assert agent_row['total_turnover'] == 120.50
        assert agent_row['today_turnover'] == 120.50

        player_row = next(x for x in c.get('/api/players', headers=auth(admin), params={'account': 'v31_turnover_player'}).json() if x['id'] == player_pk)
        assert player_row['total_recharge'] == 120.50
        assert player_row['today_recharge'] == 120.50
        assert player_row['platform_coin_balance'] == 17050

        # 结算也必须只使用这 120.50 的真实平台币流水。
        today = str(business_today())
        settlement = c.post('/api/settlements', headers=auth(admin), json={
            'agent_id': agent_pk,
            'period_start': today,
            'period_end': today,
        })
        assert settlement.status_code == 200, settlement.text
        assert settlement.json()['commission_amount'] == 30.125
        rows = c.get('/api/settlements', headers=auth(admin)).json()
        row = next(x for x in rows if x['id'] == settlement.json()['id'])
        assert row['turnover'] == 120.50


def test_blank_parent_selection_keeps_existing_agent_and_player_ownership():
    """V33: 归属编辑默认“不操作”，即使前端/客户端显式提交空字符串也不得误改归属。"""
    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        p1 = create_agent(c, admin, 'owner_keep_1', '归属一', 1, 5)
        p2 = create_agent(c, admin, 'owner_keep_2', '归属二', 1, 5)
        assert p1.status_code == 200 and p2.status_code == 200
        p1_id, p2_id = p1.json()['agent_id'], p2.json()['agent_id']

        p1_token = login(c, 'owner_keep_1', 'AgentPass123!')
        child = create_agent(c, p1_token, 'owner_keep_child', '二级归属测试', 2, 2)
        assert child.status_code == 200, child.text
        child_pk = child.json()['id']

        # 显式提交空归属必须保持当前上级不变。
        keep_agent = c.patch(f'/api/agents/{child_pk}', headers=auth(admin), json={
            'agent_name': '二级归属测试',
            'commission_rate': 0.1,
            'parent_agent_id': '',
        })
        assert keep_agent.status_code == 200, keep_agent.text
        agent_info = c.get(f'/api/agents/{child_pk}/edit-options', headers=auth(admin))
        assert agent_info.status_code == 200
        assert agent_info.json()['agent']['parent_agent_id'] == p1_id

        # 主动选择新代理后才允许改变归属。
        move_agent = c.patch(f'/api/agents/{child_pk}', headers=auth(admin), json={
            'parent_agent_id': p2_id,
        })
        assert move_agent.status_code == 200, move_agent.text
        agent_info = c.get(f'/api/agents/{child_pk}/edit-options', headers=auth(admin))
        assert agent_info.json()['agent']['parent_agent_id'] == p2_id

        # 玩家先注册到 p1，空归属同样不得把玩家误改成超管或其它代理。
        player = c.post(f'/api/public/registration/{p1_id}', json={
            'username': 'owner_keep_player',
            'password': 'PlayerPass123!',
        })
        assert player.status_code == 200, player.text
        player_pk = player.json()['id']
        keep_player = c.patch(f'/api/players/{player_pk}', headers=auth(admin), json={
            'owner_agent_id': '',
        })
        assert keep_player.status_code == 200, keep_player.text
        player_info = c.get(f'/api/players/{player_pk}/edit', headers=auth(admin))
        assert player_info.status_code == 200
        assert player_info.json()['owner_agent_id'] == p1_id

        move_player = c.patch(f'/api/players/{player_pk}', headers=auth(admin), json={
            'owner_agent_id': p2_id,
        })
        assert move_player.status_code == 200, move_player.text
        player_info = c.get(f'/api/players/{player_pk}/edit', headers=auth(admin))
        assert player_info.json()['owner_agent_id'] == p2_id


def test_player_multiple_server_roles_are_bound_and_returned_as_character_list():
    """V34: 一个玩家账号可绑定多个区服角色，列表不再拆成角色名/区服两列。"""
    from app.database import SessionLocal
    from app.models import Player, PlayerCharacter

    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        agent = create_agent(c, admin, 'multi_role_agent', '多角色代理', 1, 5, 0.1)
        assert agent.status_code == 200, agent.text
        agent_id = agent.json()['agent_id']
        reg = c.post(f'/api/public/registration/{agent_id}', json={
            'username': 'multi_role_player',
            'password': 'PlayerPass123!',
        })
        assert reg.status_code == 200, reg.text
        player_pk = reg.json()['id']

        db = SessionLocal()
        try:
            player = db.get(Player, player_pk)
            assert player is not None
            db.add_all([
                PlayerCharacter(player_id=player_pk, role_name='剑心', server_name='一区·龙城', is_primary=True),
                PlayerCharacter(player_id=player_pk, role_name='星河', server_name='二区·天启', is_primary=False),
                PlayerCharacter(player_id=player_pk, role_name='夜雨', server_name='三区·苍穹', is_primary=False),
            ])
            db.commit()
        finally:
            db.close()

        rows = c.get('/api/players', headers=auth(admin), params={'account': 'multi_role_player'})
        assert rows.status_code == 200, rows.text
        row = rows.json()[0]
        assert row['primary_role_name'] == '剑心'
        assert [x['role_name'] for x in row['characters']] == ['剑心', '星河', '夜雨']
        assert [x['server_name'] for x in row['characters']] == ['一区·龙城', '二区·天启', '三区·苍穹']
        assert row['characters'][0]['is_primary'] is True
        assert 'role_name' not in row
        assert 'server_name' not in row

        # V44: 角色查询需要匹配该玩家任意区服角色，而不只是主角色。
        by_primary_role = c.get('/api/players', headers=auth(admin), params={'role': '剑心'})
        assert by_primary_role.status_code == 200, by_primary_role.text
        assert any(x['id'] == player_pk for x in by_primary_role.json())

        by_secondary_role = c.get('/api/players', headers=auth(admin), params={'role': '星河'})
        assert by_secondary_role.status_code == 200, by_secondary_role.text
        assert any(x['id'] == player_pk for x in by_secondary_role.json())

        missing_role = c.get('/api/players', headers=auth(admin), params={'role': '不存在的角色'})
        assert missing_role.status_code == 200, missing_role.text
        assert not any(x['id'] == player_pk for x in missing_role.json())



def test_beijing_time_display_and_agent_login_timestamp():
    from datetime import datetime
    from app.main import dt

    # 数据库使用 UTC naive，后台必须转换成北京时间 UTC+8。
    assert dt(datetime(2026, 8, 10, 16, 0, 0)) == '2026-08-11 00:00:00'

    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        created = create_agent(c, admin, 'time_agent_v35', '时间代理', 1, 1)
        assert created.status_code == 200, created.text

        # 登录代理后应记录最近登录时间。
        agent_token = login(c, 'time_agent_v35', 'AgentPass123!')
        assert agent_token
        rows = c.get('/api/agents', headers=auth(admin), params={'agent_account': 'time_agent_v35'})
        assert rows.status_code == 200, rows.text
        row = rows.json()[0]
        assert row['created_at']
        assert row['last_login_at']
        # API 时间统一输出为可直接展示的北京时间格式。
        datetime.strptime(row['created_at'], '%Y-%m-%d %H:%M:%S')
        datetime.strptime(row['last_login_at'], '%Y-%m-%d %H:%M:%S')


def test_agent_list_registered_count_is_direct_registration_count():
    """V37: 代理列表注册人数只统计直接通过该代理注册链接注册的玩家。"""
    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        parent = create_agent(c, admin, 'count_parent_v37', '注册统计一级', 1, 3, 0.1)
        assert parent.status_code == 200, parent.text
        parent_id = parent.json()['agent_id']
        parent_token = login(c, 'count_parent_v37', 'AgentPass123!')

        child = create_agent(c, parent_token, 'count_child_v37', '注册统计二级', 2, 1, 0.05)
        assert child.status_code == 200, child.text
        child_id = child.json()['agent_id']

        for i in range(2):
            reg = c.post(f'/api/public/registration/{parent_id}', json={
                'username': f'count_parent_player_{i}',
                'password': 'PlayerPass123!',
            })
            assert reg.status_code == 200, reg.text

        reg = c.post(f'/api/public/registration/{child_id}', json={
            'username': 'count_child_player_0',
            'password': 'PlayerPass123!',
        })
        assert reg.status_code == 200, reg.text

        rows = c.get('/api/agents', headers=auth(admin))
        assert rows.status_code == 200, rows.text
        by_id = {row['agent_id']: row for row in rows.json()}
        assert by_id[parent_id]['registered_count'] == 2
        assert by_id[child_id]['registered_count'] == 1


def test_v38_channel_daily_turnover_filters_sort_and_agent_scope():
    """V38: 渠道结算按所选北京时间日期展示真实支付流水，并按金额降序。"""
    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')

        l1 = create_agent(c, admin, 'v38_l1', 'V38一级', 1, 2, 0.10)
        assert l1.status_code == 200, l1.text
        l1_data = l1.json()
        l1_token = login(c, 'v38_l1', 'AgentPass123!')

        l2 = create_agent(c, l1_token, 'v38_l2', 'V38二级', 2, 1, 0.20)
        assert l2.status_code == 200, l2.text
        l2_data = l2.json()
        l2_token = login(c, 'v38_l2', 'AgentPass123!')

        l3 = create_agent(c, l2_token, 'v38_l3', 'V38三级', 3, 0, 0.30)
        assert l3.status_code == 200, l3.text
        l3_data = l3.json()

        p2 = c.post(f"/api/public/registration/{l2_data['agent_id']}", json={
            'username': 'v38_player_l2', 'password': 'PlayerPass123!'
        })
        p3 = c.post(f"/api/public/registration/{l3_data['agent_id']}", json={
            'username': 'v38_player_l3', 'password': 'PlayerPass123!'
        })
        assert p2.status_code == 200, p2.text
        assert p3.status_code == 200, p3.text

        paid_l2 = c.post('/api/orders/platform', headers=auth(admin), json={
            'order_no': 'V38-PAID-L2-001', 'player_id': p2.json()['id'], 'agent_id': l2_data['id'],
            'amount': 50, 'platform_coin': 5000, 'payment_channel': 'test', 'pay_status': 'paid'
        })
        paid_l3 = c.post('/api/orders/platform', headers=auth(admin), json={
            'order_no': 'V38-PAID-L3-001', 'player_id': p3.json()['id'], 'agent_id': l3_data['id'],
            'amount': 100, 'platform_coin': 10000, 'payment_channel': 'test', 'pay_status': 'paid'
        })
        pending_l2 = c.post('/api/orders/platform', headers=auth(admin), json={
            'order_no': 'V38-PENDING-L2-001', 'player_id': p2.json()['id'], 'agent_id': l2_data['id'],
            'amount': 999, 'platform_coin': 99900, 'payment_channel': 'test', 'pay_status': 'pending'
        })
        assert paid_l2.status_code == 200, paid_l2.text
        assert paid_l3.status_code == 200, paid_l3.text
        assert pending_l2.status_code == 200, pending_l2.text

        selected = str(business_today())
        admin_rows = c.get('/api/channel-settlements', headers=auth(admin), params={
            'account': 'v38_', 'date': selected
        })
        assert admin_rows.status_code == 200, admin_rows.text
        data = admin_rows.json()
        assert data['date'] == selected
        rows = data['rows']
        assert [x['username'] for x in rows] == ['v38_l3', 'v38_l2', 'v38_l1']
        assert [x['turnover'] for x in rows] == [100.0, 50.0, 0.0]
        assert rows[0]['commission_amount'] == 30.0
        assert rows[1]['commission_amount'] == 10.0

        level_two = c.get('/api/channel-settlements', headers=auth(admin), params={
            'account': 'v38_', 'agent_level': 2, 'date': selected
        })
        assert level_two.status_code == 200, level_two.text
        assert [x['username'] for x in level_two.json()['rows']] == ['v38_l2']

        by_id = c.get('/api/channel-settlements', headers=auth(admin), params={
            'public_agent_id': l3_data['agent_id'], 'date': selected
        })
        assert by_id.status_code == 200, by_id.text
        assert [x['agent_id'] for x in by_id.json()['rows']] == [l3_data['agent_id']]

        # 普通一级代理只看自己的下级代理树，不包含自己。
        child_rows = c.get('/api/channel-settlements', headers=auth(l1_token), params={
            'account': 'v38_', 'date': selected
        })
        assert child_rows.status_code == 200, child_rows.text
        assert [x['username'] for x in child_rows.json()['rows']] == ['v38_l3', 'v38_l2']
        assert all(x['agent_id'] != l1_data['agent_id'] for x in child_rows.json()['rows'])


def test_v42_channel_settlement_total_range_columns_and_level_scope():
    """V42: 默认总流水；支持开始/结束日期；等级筛选按当前账号可见的下级层级授权。"""
    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        l1 = create_agent(c, admin, 'v40_l1_agent', 'V40一级', 1, 2, 0.12)
        assert l1.status_code == 200, l1.text
        l1_data = l1.json()
        l1_token = login(c, 'v40_l1_agent', 'AgentPass123!')

        l2 = create_agent(c, l1_token, 'v40_l2_agent', 'V40二级', 2, 1, 0.20)
        assert l2.status_code == 200, l2.text
        l2_data = l2.json()
        l2_token = login(c, 'v40_l2_agent', 'AgentPass123!')

        l3 = create_agent(c, l2_token, 'v40_l3_agent', 'V40三级', 3, 0, 0.30)
        assert l3.status_code == 200, l3.text
        l3_data = l3.json()
        l3_token = login(c, 'v40_l3_agent', 'AgentPass123!')

        player = c.post(f"/api/public/registration/{l2_data['agent_id']}", json={
            'username': 'v40_paid_player', 'password': 'PlayerPass123!'
        })
        assert player.status_code == 200, player.text
        paid = c.post('/api/orders/platform', headers=auth(admin), json={
            'order_no': 'V40-PAID-001', 'player_id': player.json()['id'], 'agent_id': l2_data['id'],
            'amount': 88, 'platform_coin': 8800, 'payment_channel': 'test', 'pay_status': 'paid'
        })
        assert paid.status_code == 200, paid.text

        # 日期默认留空：直接返回历史总流水。
        total = c.get('/api/channel-settlements', headers=auth(admin), params={'account': 'v40_'})
        assert total.status_code == 200, total.text
        total_data = total.json()
        assert total_data['period_type'] == 'total'
        assert total_data['period_label'] == '全部时间'
        assert total_data['date'] is None
        rows = total_data['rows']
        assert rows[0]['username'] == 'v40_l2_agent'
        assert rows[0]['turnover'] == 88.0
        assert rows[0]['period_label'] == '全部时间'
        assert 'parent_agent_display' not in rows[0]

        # 开始/结束日期相同：返回该北京时间自然日的流水。
        today = business_today()
        single = c.get('/api/channel-settlements', headers=auth(admin), params={
            'account': 'v40_', 'start_date': str(today), 'end_date': str(today)
        })
        assert single.status_code == 200, single.text
        single_data = single.json()
        assert single_data['period_type'] == 'day'
        assert single_data['date'] == str(today)
        assert single_data['period_label'] == str(today)
        assert single_data['rows'][0]['turnover'] == 88.0

        # V42 支持开始/结束日期区间，区间包含首尾两个北京时间自然日。
        yesterday = today - timedelta(days=1)
        ranged = c.get('/api/channel-settlements', headers=auth(admin), params={
            'account': 'v40_', 'start_date': str(yesterday), 'end_date': str(today)
        })
        assert ranged.status_code == 200, ranged.text
        ranged_data = ranged.json()
        assert ranged_data['period_type'] == 'range'
        assert ranged_data['start_date'] == str(yesterday)
        assert ranged_data['end_date'] == str(today)
        assert ranged_data['period_label'] == f'{yesterday} 至 {today}'
        assert ranged_data['rows'][0]['turnover'] == 88.0

        # 日期倒置必须拒绝，避免查询口径不明确。
        invalid_range = c.get('/api/channel-settlements', headers=auth(admin), params={
            'start_date': str(today), 'end_date': str(yesterday)
        })
        assert invalid_range.status_code == 400
        assert '开始日期不能晚于结束日期' in invalid_range.json()['detail']

        # 超管可以筛选全部三个代理等级。
        for level, username in [(1, 'v40_l1_agent'), (2, 'v40_l2_agent'), (3, 'v40_l3_agent')]:
            filtered = c.get('/api/channel-settlements', headers=auth(admin), params={
                'account': 'v40_', 'agent_level': level
            })
            assert filtered.status_code == 200, filtered.text
            assert [x['username'] for x in filtered.json()['rows']] == [username]

        # 一级代理可按自己可见的二级、三级下级代理筛选。
        l1_level2 = c.get('/api/channel-settlements', headers=auth(l1_token), params={
            'account': 'v40_', 'agent_level': 2
        })
        assert l1_level2.status_code == 200, l1_level2.text
        assert [x['username'] for x in l1_level2.json()['rows']] == ['v40_l2_agent']
        l1_level3 = c.get('/api/channel-settlements', headers=auth(l1_token), params={
            'account': 'v40_', 'agent_level': 3
        })
        assert l1_level3.status_code == 200, l1_level3.text
        assert [x['username'] for x in l1_level3.json()['rows']] == ['v40_l3_agent']

        # 二级代理只能选择三级代理；三级代理没有渠道结算权限。
        l2_level3 = c.get('/api/channel-settlements', headers=auth(l2_token), params={
            'account': 'v40_', 'agent_level': 3
        })
        assert l2_level3.status_code == 200, l2_level3.text
        assert [x['username'] for x in l2_level3.json()['rows']] == ['v40_l3_agent']
        l3_forbidden = c.get('/api/channel-settlements', headers=auth(l3_token))
        assert l3_forbidden.status_code == 403


def test_v42_settlement_frontend_has_date_range_horizontal_compact_and_seven_columns():
    """V42 前端：开始/结束日期、横向紧凑、分层等级选项、7 列固定顺序。"""
    static_dir = Path(__file__).resolve().parent.parent / 'app' / 'static'
    js = (static_dir / 'app.js').read_text(encoding='utf-8')
    css = (static_dir / 'styles.css').read_text(encoding='utf-8')
    html = (static_dir / 'index.html').read_text(encoding='utf-8')

    assert "let settlementSearch = {account:'', public_agent_id:'', agent_level:'', start_date:'', end_date:''};" in js
    assert 'settlementStartDateQuery' in js
    assert 'settlementEndDateQuery' in js
    assert "p.set('start_date',settlementSearch.start_date)" in js
    assert "p.set('end_date',settlementSearch.end_date)" in js
    assert "if(currentUser?.actor_type==='admin')return ['1','2','3'];" in js
    assert "Number(currentUser?.agent_level)===1)return ['2','3'];" in js
    assert "Number(currentUser?.agent_level)===2)return ['3'];" in js

    expected_order = [
        "['代理ID','agent_id']",
        "['代理账号','username']",
        "['代理等级','agent_level',agentLevelText]",
        "['代理名称','agent_name']",
        "[turnoverTitle,'turnover'",
        "['佣金比例','commission_rate',percent]",
        "['日期','period_label']",
    ]
    positions = [js.index(x) for x in expected_order]
    assert positions == sorted(positions)
    assert "['佣金','commission_amount'" not in js
    assert 'padding:11px 7px' in css
    assert 'grid-template-columns:128px 14px 128px' in css
    assert 'player-role-search-v44' in html

def test_v43_settlement_table_has_stable_fixed_horizontal_layout():
    """V43 渠道结算：总流水/日期查询切换时固定 7 列宽度，避免左右跳动。"""
    static_dir = Path(__file__).resolve().parent.parent / 'app' / 'static'
    css = (static_dir / 'styles.css').read_text(encoding='utf-8')
    html = (static_dir / 'index.html').read_text(encoding='utf-8')

    assert '.settlement-table-scroll table{' in css
    assert 'table-layout:fixed' in css
    assert 'min-width:860px' in css
    assert 'scrollbar-gutter:stable' in css
    for idx, width in [(1, '9%'), (2, '15%'), (3, '11%'), (4, '18%'), (5, '15%'), (6, '12%'), (7, '20%')]:
        assert f'th:nth-child({idx}),.settlement-table-scroll td:nth-child({idx}){{width:{width}' in css
    assert 'player-role-search-v44' in html



def test_v44_player_role_search_frontend_and_api_contract():
    """V44 玩家列表增加角色查询，并保持查询框紧凑。"""
    static_dir = Path(__file__).resolve().parent.parent / 'app' / 'static'
    js = (static_dir / 'app.js').read_text(encoding='utf-8')
    css = (static_dir / 'styles.css').read_text(encoding='utf-8')
    assert "let playerSearch = {account:'', role:'', parent:''};" in js
    assert '<label>角色查询</label>' in js
    assert 'id="playerRoleQuery"' in js
    assert "p.set('role',playerSearch.role)" in js
    assert "['#playerAccountQuery','#playerRoleQuery','#playerParentQuery']" in js
    assert '.player-search-bar>.query-field{flex:0 0 190px;width:190px' in css
