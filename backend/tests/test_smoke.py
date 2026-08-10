import os
from pathlib import Path

TEST_DB = Path(__file__).resolve().parent.parent / "test_cps.db"
if TEST_DB.exists():
    TEST_DB.unlink()

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "ChangeMe123!"

from fastapi.testclient import TestClient
from app.main import app


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
        assert row['role_name'] == '未绑定'
        assert row['server_name'] == '未绑定'

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

        today = str(date.today())
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
