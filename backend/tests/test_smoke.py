import os
from datetime import timedelta
from pathlib import Path

TEST_DB = Path(__file__).resolve().parent.parent / "test_cps.db"
if TEST_DB.exists():
    TEST_DB.unlink()

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "ChangeMe123!"
os.environ["PAYMENT_CALLBACK_SECRET"] = "test-payment-secret"

from fastapi.testclient import TestClient
from app.main import app, business_today, sync_real_payment_aggregates
from app.database import SessionLocal
from app.models import Player, PlayerCharacter, PlatformCoinOrder, PlayerCoinLedger, MallOrder, RedemptionCode, PrivilegeCardPurchase, PrivilegeCardClaim, AdminIPWhitelist, AdminLoginIPState


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


def platform_payment(c, payload):
    """V46 测试辅助：模拟玩家充值系统下单，并在 paid 时模拟支付成功回调。"""
    payload = dict(payload)
    player_id = payload.pop('player_id')
    payload.pop('agent_id', None)  # 订单归属必须从玩家当前归属自动获取，外部不能伪造。
    pay_status = payload.pop('pay_status', 'paid')
    channel = str(payload.pop('payment_channel', 'wechat')).lower()
    method = 'alipay' if channel in {'alipay', 'ali', '支付宝'} else 'wechat'
    db = SessionLocal()
    try:
        player = db.get(Player, player_id)
        assert player is not None
        player_account = player.username
    finally:
        db.close()
    create = c.post('/api/payment/platform-orders', headers={'X-Payment-Secret': 'test-payment-secret'}, json={
        'order_no': payload['order_no'],
        'player_account': player_account,
        'product_name': payload.pop('product_name', '平台币充值'),
        'amount': payload['amount'],
        'platform_coin': payload['platform_coin'],
        'payment_method': method,
    })
    if create.status_code != 200 or pay_status != 'paid':
        return create
    return c.post('/api/payment/platform-orders/paid', headers={'X-Payment-Secret': 'test-payment-secret'}, json={
        'order_no': payload['order_no'],
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
        order = platform_payment(c, {
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
            assert key not in l1_dashboard_data
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
            'name': 'V23流水测试礼包',
            'category': 'gift',
            'price': 68,
            'stock': 10,
            'description': 'dashboard turnover scope test',
        })
        assert product.status_code == 200, product.text
        product_pk = product.json()['id']

        paid_platform = platform_payment(c, {
            'order_no': 'V23-PLATFORM-PAID-001',
            'player_id': player_pk,
            'amount': 123.45,
            'platform_coin': 12345,
            'payment_channel': 'manual',
            'pay_status': 'paid',
        })
        assert paid_platform.status_code == 200, paid_platform.text

        pending_platform = platform_payment(c, {
            'order_no': 'V23-PLATFORM-PENDING-001',
            'player_id': player_pk,
            'amount': 999.00,
            'platform_coin': 99900,
            'payment_channel': 'manual',
            'pay_status': 'pending',
        })
        assert pending_platform.status_code == 200, pending_platform.text

        player_login = c.post('/api/player/auth/login', json={'username': 'v23_flow_player', 'password': 'PlayerPass123!'})
        assert player_login.status_code == 200, player_login.text
        player_token = player_login.json()['access_token']
        paid_mall = c.post(f'/api/player/mall/purchase/{product_pk}', headers=auth(player_token), json={'quantity': 1})
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
        paid = platform_payment(c, {
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
            'name': 'V31流水隔离礼包',
            'category': 'gift',
            'price': 67,
            'stock': 5,
            'description': 'mall should not count as turnover',
        })
        assert product.status_code == 200, product.text
        player_login = c.post('/api/player/auth/login', json={'username': 'v31_turnover_player', 'password': 'PlayerPass123!'})
        assert player_login.status_code == 200, player_login.text
        mall = c.post(f"/api/player/mall/purchase/{product.json()['id']}", headers=auth(player_login.json()['access_token']), json={'quantity': 1})
        assert mall.status_code == 200, mall.text

        after_mall = c.get('/api/dashboard', headers=auth(admin)).json()
        assert after_mall['total_turnover'] == before['total_turnover']
        player_row = next(x for x in c.get('/api/players', headers=auth(admin), params={'account': 'v31_turnover_player'}).json() if x['id'] == player_pk)
        # V54：累计充值只来自商城消费；真实平台币充值不会增加该累计值。
        assert player_row['total_recharge'] == 67
        assert player_row['today_recharge'] == 0

        # 只有真实已支付的平台币订单进入流水。
        paid = platform_payment(c, {
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
        assert player_row['total_recharge'] == 67
        assert player_row['today_recharge'] == 120.50
        assert player_row['platform_coin_balance'] == 16983

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

        paid_l2 = platform_payment(c, {
            'order_no': 'V38-PAID-L2-001', 'player_id': p2.json()['id'], 'agent_id': l2_data['id'],
            'amount': 50, 'platform_coin': 5000, 'payment_channel': 'test', 'pay_status': 'paid'
        })
        paid_l3 = platform_payment(c, {
            'order_no': 'V38-PAID-L3-001', 'player_id': p3.json()['id'], 'agent_id': l3_data['id'],
            'amount': 100, 'platform_coin': 10000, 'payment_channel': 'test', 'pay_status': 'paid'
        })
        pending_l2 = platform_payment(c, {
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
        paid = platform_payment(c, {
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
    assert 'platform-orders-v46' in html

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
    assert 'platform-orders-v46' in html



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


def test_v45_settlement_vertical_row_spacing_matches_agent_table():
    """V45 渠道结算与下级渠道使用同一表头/数据行高度，横向紧凑 padding 保持不变。"""
    static_dir = Path(__file__).resolve().parent.parent / 'app' / 'static'
    css = (static_dir / 'styles.css').read_text(encoding='utf-8')
    html = (static_dir / 'index.html').read_text(encoding='utf-8')
    assert '.agent-table-scroll thead tr,\n.settlement-table-scroll thead tr{height:38px}' in css
    assert '.agent-table-scroll tbody tr,\n.settlement-table-scroll tbody tr{height:52px}' in css
    assert '.agent-table-scroll tbody td,\n.settlement-table-scroll tbody td{vertical-align:middle;padding-top:11px;padding-bottom:11px}' in css
    assert 'padding:11px 7px' in css
    assert 'platform-orders-v46' in html



def test_v46_platform_orders_are_payment_generated_searchable_and_no_manual_add():
    """V46：平台币订单由充值/支付流程自动生成，后台只能查单和补发。"""
    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        agent_resp = create_agent(c, admin, 'v46_order_agent', 'V46订单代理', 1, 2, 0.12)
        assert agent_resp.status_code == 200, agent_resp.text
        agent_id = agent_resp.json()['agent_id']
        player = c.post(f'/api/public/registration/{agent_id}', json={
            'username': 'v46_order_player', 'password': 'PlayerPass123!'
        })
        assert player.status_code == 200, player.text

        # 后台手工新增必须被禁止。
        manual = c.post('/api/orders/platform', headers=auth(admin), json={})
        assert manual.status_code == 405
        assert '后台禁止手工添加' in manual.json()['detail']

        # 充值系统创建未支付订单；归属由玩家自动带出，而不是外部传 agent_id。
        created = c.post('/api/payment/platform-orders', headers={'X-Payment-Secret': 'test-payment-secret'}, json={
            'order_no': 'V46-WX-ORDER-001',
            'player_account': 'v46_order_player',
            'product_name': '100元平台币礼包',
            'amount': 100,
            'platform_coin': 10000,
            'payment_method': 'wechat',
        })
        assert created.status_code == 200, created.text
        assert created.json()['status'] == 'unpaid'

        unpaid = c.get('/api/orders/platform', headers=auth(admin), params={
            'order_no': 'WX-ORDER', 'account': 'v46_order', 'payment_method': 'wechat', 'status': 'unpaid'
        })
        assert unpaid.status_code == 200, unpaid.text
        assert len(unpaid.json()) == 1
        assert unpaid.json()[0]['player_account'] == 'v46_order_player'
        assert unpaid.json()[0]['product_name'] == '100元平台币礼包'
        assert unpaid.json()[0]['payment_method'] == 'wechat'
        assert unpaid.json()[0]['status'] == 'unpaid'

        # 支付成功回调自动计真实流水并自动发货，重复回调不重复增加余额/流水。
        paid = c.post('/api/payment/platform-orders/paid', headers={'X-Payment-Secret': 'test-payment-secret'}, json={
            'order_no': 'V46-WX-ORDER-001'
        })
        assert paid.status_code == 200, paid.text
        assert paid.json()['status'] == 'paid'
        assert paid.json()['delivery_status'] == 'success'
        again = c.post('/api/payment/platform-orders/paid', headers={'X-Payment-Secret': 'test-payment-secret'}, json={
            'order_no': 'V46-WX-ORDER-001'
        })
        assert again.status_code == 200, again.text

        paid_rows = c.get('/api/orders/platform', headers=auth(admin), params={'status': 'paid'})
        row = next(x for x in paid_rows.json() if x['order_no'] == 'V46-WX-ORDER-001')
        assert row['amount'] == 100.0
        assert row['status'] == 'paid'
        assert row['delivery_status'] == 'success'
        assert row['created_at']
        assert row['paid_at']

        player_rows = c.get('/api/players', headers=auth(admin), params={'account': 'v46_order_player'}).json()
        p = next(x for x in player_rows if x['username'] == 'v46_order_player')
        assert p['platform_coin_balance'] == 10000
        assert p['total_recharge'] == 0

        # 模拟“支付已成功，但实际发货失败”的状态：补发只恢复发货，不重复增加流水，也不增加累计充值。
        turnover_before_resend = c.get('/api/dashboard', headers=auth(admin)).json()['total_turnover']
        db = SessionLocal()
        try:
            order = db.get(PlatformCoinOrder, row['id'])
            failed_player = db.get(Player, order.player_id)
            failed_player.platform_coin_balance -= int(order.platform_coin)
            # V49：真正的发货失败必须不存在该订单的成功入账凭证；否则补发会按幂等规则认定已到账。
            db.query(PlayerCoinLedger).filter(
                PlayerCoinLedger.player_id == order.player_id,
                PlayerCoinLedger.action == 'recharge',
                PlayerCoinLedger.note == f'平台币订单 {order.order_no}',
            ).delete(synchronize_session=False)
            order.delivery_status = 'failed'
            order.delivery_message = '模拟游戏服发货失败'
            order.delivered_at = None
            db.commit()
        finally:
            db.close()
        resend = c.post(f"/api/orders/platform/{row['id']}/resend", headers=auth(admin))
        assert resend.status_code == 200, resend.text
        after = c.get('/api/players', headers=auth(admin), params={'account': 'v46_order_player'}).json()[0]
        assert after['platform_coin_balance'] == 10000
        assert after['total_recharge'] == 0
        assert c.get('/api/dashboard', headers=auth(admin)).json()['total_turnover'] == turnover_before_resend

        # 补发成功以后再次点击会被拒绝，避免重复到账。
        duplicate_resend = c.post(f"/api/orders/platform/{row['id']}/resend", headers=auth(admin))
        assert duplicate_resend.status_code == 409

        # 支付回调密钥错误必须拒绝。
        bad = c.post('/api/payment/platform-orders', headers={'X-Payment-Secret': 'wrong'}, json={
            'player_account': 'v46_order_player', 'product_name': '测试', 'amount': 1,
            'platform_coin': 100, 'payment_method': 'alipay'
        })
        assert bad.status_code == 401


def test_v46_platform_order_frontend_has_required_filters_columns_and_resend():
    static_dir = Path(__file__).resolve().parent.parent / 'app' / 'static'
    js = (static_dir / 'app.js').read_text(encoding='utf-8')
    css = (static_dir / 'styles.css').read_text(encoding='utf-8')
    html = (static_dir / 'index.html').read_text(encoding='utf-8')
    for text in ['订单号查询','账号查询','支付方式','微信','支付宝','未支付','已支付','商品名称','金额（元）','发货','创建时间','支付时间','补发']:
        assert text in js
    assert "if(view==='platformOrders') return renderPlatformOrders();" in js
    assert '新增平台币订单' not in js
    assert '.platform-order-search-bar' in css
    assert '.platform-order-table-scroll table' in css
    assert 'table-layout:fixed' in css
    assert 'platform-orders-v46' in html


def test_v47_superadmin_payment_test_full_flow_and_rbac():
    """V47：超管支付测试页能完整模拟下单/支付，普通代理无权访问。"""
    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        agent_resp = create_agent(c, admin, 'v47_test_agent', 'V47测试代理', 1, 2, 0.1)
        assert agent_resp.status_code == 200, agent_resp.text
        agent_public_id = agent_resp.json()['agent_id']
        reg = c.post(f'/api/public/registration/{agent_public_id}', json={
            'username': 'v47_test_player', 'password': 'PlayerPass123!'
        })
        assert reg.status_code == 200, reg.text

        me = c.get('/api/auth/me', headers=auth(admin))
        assert me.status_code == 200
        assert 'payment.test' in me.json()['permissions']

        search = c.get('/api/payment-test/players', headers=auth(admin), params={'keyword': 'v47_test'})
        assert search.status_code == 200, search.text
        assert len(search.json()) == 1
        assert search.json()[0]['username'] == 'v47_test_player'

        created = c.post('/api/payment-test/orders', headers=auth(admin), json={
            'player_account': 'v47_test_player',
            'product_name': 'V47测试100币',
            'amount': 1,
            'platform_coin': 100,
            'payment_method': 'wechat',
        })
        assert created.status_code == 200, created.text
        order_no = created.json()['order_no']
        assert order_no.startswith('TESTPC')
        assert created.json()['status'] == 'unpaid'

        paid = c.post(f'/api/payment-test/orders/{order_no}/pay', headers=auth(admin))
        assert paid.status_code == 200, paid.text
        assert paid.json()['status'] == 'paid'
        assert paid.json()['delivery_status'] == 'success'

        rows = c.get('/api/orders/platform', headers=auth(admin), params={'order_no': order_no}).json()
        assert len(rows) == 1
        assert rows[0]['status'] == 'paid'
        player = c.get('/api/players', headers=auth(admin), params={'account': 'v47_test_player'}).json()[0]
        assert player['platform_coin_balance'] == 100
        assert player['total_recharge'] == 0

        # 测试页不能拿正式订单号直接伪造测试支付。
        formal = c.post('/api/payment/platform-orders', headers={'X-Payment-Secret': 'test-payment-secret'}, json={
            'order_no': 'V47-FORMAL-ORDER', 'player_account': 'v47_test_player', 'product_name': '正式订单',
            'amount': 2, 'platform_coin': 200, 'payment_method': 'alipay'
        })
        assert formal.status_code == 200, formal.text
        blocked = c.post('/api/payment-test/orders/V47-FORMAL-ORDER/pay', headers=auth(admin))
        assert blocked.status_code == 403

        agent_token = login(c, 'v47_test_agent', 'AgentPass123!')
        assert c.get('/api/payment-test/players', headers=auth(agent_token)).status_code == 403
        assert c.post('/api/payment-test/orders', headers=auth(agent_token), json={
            'player_account': 'v47_test_player', 'product_name': '越权', 'amount': 1,
            'platform_coin': 1, 'payment_method': 'wechat'
        }).status_code == 403


def test_v47_payment_test_frontend_is_superadmin_only():
    static_dir = Path(__file__).resolve().parent.parent / 'app' / 'static'
    js = (static_dir / 'app.js').read_text(encoding='utf-8')
    css = (static_dir / 'styles.css').read_text(encoding='utf-8')
    html = (static_dir / 'index.html').read_text(encoding='utf-8')
    for text in ['支付测试','模拟下单','模拟支付成功','搜索玩家','测试平台币充值']:
        assert text in js or text in html
    assert 'data-view="paymentTest" data-permission="payment.test"' in html
    assert "paymentTest:'payment.test'" in js
    assert "if(view==='paymentTest') return renderPaymentTest();" in js
    assert '.payment-test-grid' in css
    assert 'payment-test-v47' in html


def test_v48_platform_status_only_unpaid_paid_and_delivery_separate():
    """V48：平台币订单状态只表示支付状态，发货结果独立展示。"""
    static_dir = Path(__file__).resolve().parent.parent / 'app' / 'static'
    js = (static_dir / 'app.js').read_text(encoding='utf-8')
    html = (static_dir / 'index.html').read_text(encoding='utf-8')
    assert '<option value="unpaid"' in js
    assert '<option value="paid"' in js
    assert '<option value="shipped"' not in js
    assert "shipped:['已发货'" not in js
    assert 'platform-status-v48' in html

    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        agent_resp = create_agent(c, admin, 'v48_status_agent', 'V48状态代理', 1, 2, 0.1)
        assert agent_resp.status_code == 200, agent_resp.text
        agent_public_id = agent_resp.json()['agent_id']
        reg = c.post(f'/api/public/registration/{agent_public_id}', json={
            'username': 'v48_status_player', 'password': 'PlayerPass123!'
        })
        assert reg.status_code == 200, reg.text
        created = c.post('/api/payment/platform-orders', headers={'X-Payment-Secret': 'test-payment-secret'}, json={
            'order_no': 'V48-STATUS-ORDER', 'player_account': 'v48_status_player',
            'product_name': 'V48状态测试', 'amount': 3, 'platform_coin': 300, 'payment_method': 'wechat'
        })
        assert created.status_code == 200, created.text
        paid = c.post('/api/payment/platform-orders/paid', headers={'X-Payment-Secret': 'test-payment-secret'}, json={
            'order_no': 'V48-STATUS-ORDER'
        })
        assert paid.status_code == 200, paid.text
        assert paid.json()['status'] == 'paid'
        assert paid.json()['delivery_status'] == 'success'
        rows = c.get('/api/orders/platform', headers=auth(admin), params={'status': 'paid', 'order_no': 'V48-STATUS-ORDER'})
        assert rows.status_code == 200, rows.text
        assert len(rows.json()) == 1
        assert rows.json()[0]['status'] == 'paid'
        assert rows.json()[0]['delivery_status'] == 'success'
        invalid = c.get('/api/orders/platform', headers=auth(admin), params={'status': 'shipped'})
        assert invalid.status_code == 400
        assert invalid.json()['detail'] == '状态只支持未支付、已支付'


def test_v49_delivery_status_requires_actual_credit_confirmation():
    """V49：支付成功≠发货成功；只有实际余额入账事务提交成功才显示发货成功。"""
    static_dir = Path(__file__).resolve().parent.parent / 'app' / 'static'
    html = (static_dir / 'index.html').read_text(encoding='utf-8')
    js = (static_dir / 'app.js').read_text(encoding='utf-8')
    assert 'delivery-confirm-v49' in html
    assert 'delivery_message' in js

    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        agent_resp = create_agent(c, admin, 'v49_delivery_agent', 'V49发货代理', 1, 2, 0.1)
        assert agent_resp.status_code == 200, agent_resp.text
        agent_public_id = agent_resp.json()['agent_id']
        reg = c.post(f'/api/public/registration/{agent_public_id}', json={
            'username': 'v49_delivery_player', 'password': 'PlayerPass123!'
        })
        assert reg.status_code == 200, reg.text

        # 正常订单：支付成功后必须有余额实际增加与充值流水凭证，才是发货成功。
        db = SessionLocal()
        try:
            player = db.query(Player).filter(Player.username == 'v49_delivery_player').first()
            before_balance = int(player.platform_coin_balance or 0)
        finally:
            db.close()
        created = c.post('/api/payment/platform-orders', headers={'X-Payment-Secret': 'test-payment-secret'}, json={
            'order_no': 'V49-DELIVERY-OK', 'player_account': 'v49_delivery_player',
            'product_name': 'V49到账确认', 'amount': 5, 'platform_coin': 500, 'payment_method': 'wechat'
        })
        assert created.status_code == 200, created.text
        paid = c.post('/api/payment/platform-orders/paid', headers={'X-Payment-Secret': 'test-payment-secret'}, json={
            'order_no': 'V49-DELIVERY-OK'
        })
        assert paid.status_code == 200, paid.text
        assert paid.json()['status'] == 'paid'
        assert paid.json()['delivery_status'] == 'success'

        db = SessionLocal()
        try:
            player = db.query(Player).filter(Player.username == 'v49_delivery_player').first()
            assert int(player.platform_coin_balance or 0) == before_balance + 500
            ledger = db.query(PlayerCoinLedger).filter(
                PlayerCoinLedger.player_id == player.id,
                PlayerCoinLedger.action == 'recharge',
                PlayerCoinLedger.note == '平台币订单 V49-DELIVERY-OK',
            ).first()
            assert ledger is not None
            assert int(ledger.delta) == 500
        finally:
            db.close()

        # 异常订单：即使支付已确认，实际发货条件失败时也必须保持“已支付 + 发货失败”。
        created2 = c.post('/api/payment/platform-orders', headers={'X-Payment-Secret': 'test-payment-secret'}, json={
            'order_no': 'V49-DELIVERY-FAIL', 'player_account': 'v49_delivery_player',
            'product_name': 'V49失败确认', 'amount': 2, 'platform_coin': 200, 'payment_method': 'alipay'
        })
        assert created2.status_code == 200, created2.text
        db = SessionLocal()
        try:
            order = db.query(PlatformCoinOrder).filter(PlatformCoinOrder.order_no == 'V49-DELIVERY-FAIL').first()
            order.platform_coin = 0  # 模拟发货侧数据异常；支付本身仍可成功。
            db.commit()
        finally:
            db.close()
        paid2 = c.post('/api/payment/platform-orders/paid', headers={'X-Payment-Secret': 'test-payment-secret'}, json={
            'order_no': 'V49-DELIVERY-FAIL'
        })
        assert paid2.status_code == 200, paid2.text
        assert paid2.json()['status'] == 'paid'
        assert paid2.json()['delivery_status'] == 'failed'
        rows = c.get('/api/orders/platform', headers=auth(admin), params={'order_no': 'V49-DELIVERY-FAIL'})
        assert rows.status_code == 200, rows.text
        row = rows.json()[0]
        assert row['status'] == 'paid'
        assert row['delivery_status'] == 'failed'
        assert '平台币数量无效' in row['delivery_message']


def test_v50_platform_orders_date_range_and_beijing_created_date_filter():
    """V50：平台币订单支持开始/结束日期；后端按北京时间创建日期筛选。"""
    static_dir = Path(__file__).resolve().parent.parent / 'app' / 'static'
    js = (static_dir / 'app.js').read_text(encoding='utf-8')
    css = (static_dir / 'styles.css').read_text(encoding='utf-8')
    html = (static_dir / 'index.html').read_text(encoding='utf-8')
    assert 'platformStartDateQuery' in js
    assert 'platformEndDateQuery' in js
    assert '开始日期' in js and '结束日期' in js
    assert '.platform-order-search-bar>.platform-date-field' in css
    assert 'platform-all-orders-v51' in html

    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        agent_resp = create_agent(c, admin, 'v50_date_agent', 'V50日期代理', 1, 2, 0.1)
        assert agent_resp.status_code == 200, agent_resp.text
        agent_public_id = agent_resp.json()['agent_id']
        reg = c.post(f'/api/public/registration/{agent_public_id}', json={
            'username': 'v50_date_player', 'password': 'PlayerPass123!'
        })
        assert reg.status_code == 200, reg.text
        for no in ['V50-DATE-TODAY', 'V50-DATE-OLD']:
            created = c.post('/api/payment/platform-orders', headers={'X-Payment-Secret': 'test-payment-secret'}, json={
                'order_no': no, 'player_account': 'v50_date_player', 'product_name': 'V50日期测试',
                'amount': 1, 'platform_coin': 100, 'payment_method': 'wechat'
            })
            assert created.status_code == 200, created.text

        # 把其中一单移动到北京时间昨天，验证当天查询不会命中。
        db = SessionLocal()
        try:
            old = db.query(PlatformCoinOrder).filter(PlatformCoinOrder.order_no == 'V50-DATE-OLD').first()
            old.created_at = old.created_at - timedelta(days=1)
            db.commit()
        finally:
            db.close()

        today = business_today().isoformat()
        yesterday = (business_today() - timedelta(days=1)).isoformat()
        today_rows = c.get('/api/orders/platform', headers=auth(admin), params={
            'account': 'v50_date_player', 'start_date': today, 'end_date': today
        })
        assert today_rows.status_code == 200, today_rows.text
        assert {x['order_no'] for x in today_rows.json()} == {'V50-DATE-TODAY'}

        range_rows = c.get('/api/orders/platform', headers=auth(admin), params={
            'account': 'v50_date_player', 'start_date': yesterday, 'end_date': today
        })
        assert range_rows.status_code == 200, range_rows.text
        assert {x['order_no'] for x in range_rows.json()} == {'V50-DATE-TODAY', 'V50-DATE-OLD'}

        invalid = c.get('/api/orders/platform', headers=auth(admin), params={
            'start_date': today, 'end_date': yesterday
        })
        assert invalid.status_code == 400
        assert invalid.json()['detail'] == '开始日期不能晚于结束日期'


def test_v51_platform_orders_default_all_and_created_at_desc():
    """V51：默认日期为空，显示全部订单，并按创建时间从新到旧排序。"""
    static_dir = Path(__file__).resolve().parent.parent / 'app' / 'static'
    js = (static_dir / 'app.js').read_text(encoding='utf-8')
    html = (static_dir / 'index.html').read_text(encoding='utf-8')
    assert "let platformOrderSearch = {order_no:'', account:'', payment_method:'', status:'', start_date:'', end_date:''};" in js
    assert "platformOrderSearch={order_no:'',account:'',payment_method:'',status:'',start_date:'',end_date:''}" in js
    assert '默认显示全部订单并按创建时间从新到旧排序' in js
    assert 'platform-all-orders-v51' in html

    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        agent_resp = create_agent(c, admin, 'v51_order_agent', 'V51订单代理', 1, 2, 0.1)
        assert agent_resp.status_code == 200, agent_resp.text
        aid = agent_resp.json()['agent_id']
        reg = c.post(f'/api/public/registration/{aid}', json={
            'username': 'v51_order_player', 'password': 'PlayerPass123!'
        })
        assert reg.status_code == 200, reg.text
        for no in ['V51-OLDER', 'V51-NEWER']:
            created = c.post('/api/payment/platform-orders', headers={'X-Payment-Secret': 'test-payment-secret'}, json={
                'order_no': no, 'player_account': 'v51_order_player', 'product_name': 'V51排序测试',
                'amount': 1, 'platform_coin': 100, 'payment_method': 'wechat'
            })
            assert created.status_code == 200, created.text

        db = SessionLocal()
        try:
            older = db.query(PlatformCoinOrder).filter(PlatformCoinOrder.order_no == 'V51-OLDER').first()
            newer = db.query(PlatformCoinOrder).filter(PlatformCoinOrder.order_no == 'V51-NEWER').first()
            older.created_at = newer.created_at - timedelta(days=2)
            db.commit()
        finally:
            db.close()

        rows = c.get('/api/orders/platform', headers=auth(admin), params={'account': 'v51_order_player'})
        assert rows.status_code == 200, rows.text
        data = rows.json()
        assert [x['order_no'] for x in data[:2]] == ['V51-NEWER', 'V51-OLDER']


def test_v52_player_center_mall_purchase_auto_creates_order_and_blocks_manual_order():
    """V52：注册后进入玩家中心；玩家用平台币买礼包自动生成商城订单，后台禁止手工造单。"""
    static_dir = Path(__file__).resolve().parent.parent / 'app' / 'static'
    register_html = (static_dir / 'register.html').read_text(encoding='utf-8')
    center_html = (static_dir / 'player_center.html').read_text(encoding='utf-8')
    app_js = (static_dir / 'app.js').read_text(encoding='utf-8')
    assert "player_center_path||'/player'" in register_html
    assert '玩家中心' in center_html and '/api/player/mall/purchase/' in center_html
    assert "if(view==='mallOrders') return renderMallOrders();" in app_js
    assert "['累计充值','total_recharge']" in app_js
    assert '新增商城订单' not in app_js.split("if(view==='mallOrders')", 1)[1].split("if(view==='shipments')", 1)[0]

    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        agent_resp = create_agent(c, admin, 'v52_mall_agent', 'V52商城代理', 1, 2, 0.1)
        assert agent_resp.status_code == 200, agent_resp.text
        agent_id = agent_resp.json()['agent_id']

        reg = c.post(f'/api/public/registration/{agent_id}', json={
            'username': 'v52_mall_player', 'password': 'PlayerPass123!'
        })
        assert reg.status_code == 200, reg.text
        assert reg.json()['player_center_path'] == '/player'
        player_pk = reg.json()['id']
        page = c.get('/player')
        assert page.status_code == 200
        assert '玩家登录' not in page.text and '账号与注册时填写的玩家账号一致。' not in page.text and '购买礼包' in page.text

        player_login = c.post('/api/player/auth/login', json={
            'username': 'v52_mall_player', 'password': 'PlayerPass123!'
        })
        assert player_login.status_code == 200, player_login.text
        player_token = player_login.json()['access_token']
        assert isinstance(player_login.json().get('characters'), list)
        fast_me = c.get('/api/player/me?include_cumulative=false', headers=auth(player_token))
        assert fast_me.status_code == 200, fast_me.text
        assert 'today_cumulative_recharge' not in fast_me.json()
        assert 'permanent_cumulative_recharge' not in fast_me.json()
        assert player_login.json()['actor_type'] == 'player'

        # 玩家 token 与代理后台 token 隔离。
        denied = c.get('/api/dashboard', headers=auth(player_token))
        assert denied.status_code == 403

        # 超管给测试玩家准备平台币；这仍然不是充值流水。
        issue = c.patch(f'/api/players/{player_pk}', headers=auth(admin), json={
            'coin_action': 'issue', 'coin_amount': 1000
        })
        assert issue.status_code == 200, issue.text
        before_turnover = c.get('/api/dashboard', headers=auth(admin)).json()['total_turnover']

        gift = c.post('/api/products', headers=auth(admin), json={
            'sku': 'V52-GIFT-001', 'name': 'V52网页礼包', 'category': 'gift',
            'price': 250, 'stock': 3, 'description': '玩家中心测试礼包'
        })
        assert gift.status_code == 200, gift.text
        gift_id = gift.json()['id']
        # 普通 product 分类不应出现在玩家中心礼包商城。
        other = c.post('/api/products', headers=auth(admin), json={
            'sku': 'V52-PRODUCT-001', 'name': '非礼包商品', 'category': 'product',
            'price': 99, 'stock': 3, 'description': ''
        })
        assert other.status_code == 200

        products = c.get('/api/player/mall/products', headers=auth(player_token))
        assert products.status_code == 200, products.text
        assert any(x['id'] == gift_id and x['coin_price'] == 250 for x in products.json())
        assert all(x['name'] != '非礼包商品' for x in products.json())

        # V63：商城每次固定只能购买 1 个礼包，批量购买由后端直接拒绝。
        multi = c.post(f'/api/player/mall/purchase/{gift_id}', headers=auth(player_token), json={'quantity': 2})
        assert multi.status_code == 422
        buy = c.post(f'/api/player/mall/purchase/{gift_id}', headers=auth(player_token), json={'quantity': 1})
        assert buy.status_code == 200, buy.text
        assert buy.json()['platform_coin_balance'] == 750
        assert buy.json()['order_no'].startswith('MO')
        order_no = buy.json()['order_no']

        me = c.get('/api/player/me', headers=auth(player_token))
        assert me.status_code == 200
        assert me.json()['platform_coin_balance'] == 750
        # V61：概览当日/永久累充都只来自商城平台币消费。
        assert me.json()['today_cumulative_recharge'] == 250.0
        assert me.json()['permanent_cumulative_recharge'] == 250.0

        # V53：商城消费增加累计充值奖励进度，但不增加今日真实充值。
        player_row = next(x for x in c.get('/api/players', headers=auth(admin), params={'account': 'v52_mall_player'}).json() if x['id'] == player_pk)
        assert player_row['total_recharge'] == 250
        assert player_row['today_recharge'] == 0

        own_orders = c.get('/api/player/mall/orders', headers=auth(player_token))
        assert own_orders.status_code == 200
        own = next(x for x in own_orders.json() if x['order_no'] == order_no)
        assert own['product_name'] == 'V52网页礼包'
        assert own['quantity'] == 1
        assert own['coin_amount'] == 250
        assert own['pay_status'] == 'paid'
        assert own['delivery_status'] == 'waiting'

        admin_orders = c.get('/api/orders/mall', headers=auth(admin))
        assert admin_orders.status_code == 200, admin_orders.text
        admin_order = next(x for x in admin_orders.json() if x['order_no'] == order_no)
        assert admin_order['player_account'] == 'v52_mall_player'
        assert admin_order['product_name'] == 'V52网页礼包'
        assert admin_order['coin_amount'] == 250

        manual = c.post('/api/orders/mall', headers=auth(admin), json={
            'order_no': 'MANUAL-V52', 'player_id': player_pk, 'product_id': gift_id,
            'quantity': 1, 'amount': 250, 'pay_status': 'paid'
        })
        assert manual.status_code == 405
        assert '后台禁止手工添加' in manual.json()['detail']

        # 商城消费会增加累充奖励进度，但不进入真实支付流水/佣金。
        after_turnover = c.get('/api/dashboard', headers=auth(admin)).json()['total_turnover']
        assert after_turnover == before_turnover

        rule = c.post('/api/recharge-rules', headers=auth(admin), json={
            'name': 'V53商城累充250', 'threshold_amount': 250, 'reward_content': 'V53测试奖励'
        })
        assert rule.status_code == 200, rule.text
        claim = c.post('/api/claims', headers=auth(admin), json={
            'player_id': player_pk, 'rule_id': rule.json()['id']
        })
        assert claim.status_code == 200, claim.text

        db = SessionLocal()
        try:
            ledger = db.query(PlayerCoinLedger).filter(
                PlayerCoinLedger.player_id == player_pk,
                PlayerCoinLedger.action == 'mall_purchase',
                PlayerCoinLedger.note.like(f'%{order_no}%'),
            ).first()
            assert ledger is not None
            assert ledger.delta == -250
        finally:
            db.close()


def test_v54_real_platform_payment_does_not_increase_cumulative_recharge_but_mall_spend_does():
    """V54：真实平台币充值只计流水/分佣；累计充值只由网页商城实际消费增加。"""
    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        agent_resp = create_agent(c, admin, 'v54_agent', 'V54代理', 1, 2, 0.2)
        assert agent_resp.status_code == 200, agent_resp.text
        agent = agent_resp.json()
        reg = c.post(f"/api/public/registration/{agent['agent_id']}", json={
            'username': 'v54_player', 'password': 'PlayerPass123!'
        })
        assert reg.status_code == 200, reg.text
        player_pk = reg.json()['id']

        paid = platform_payment(c, {
            'order_no': 'V54-PAID-001', 'player_id': player_pk, 'agent_id': agent['id'],
            'amount': 88, 'platform_coin': 8800, 'payment_channel': 'wechat', 'pay_status': 'paid',
        })
        assert paid.status_code == 200, paid.text
        after_paid = c.get('/api/players', headers=auth(admin), params={'account': 'v54_player'}).json()[0]
        assert after_paid['today_recharge'] == 88.0
        assert after_paid['total_recharge'] == 0
        agent_row = c.get('/api/agents', headers=auth(admin), params={'public_agent_id': agent['agent_id']}).json()[0]
        assert agent_row['total_turnover'] == 88.0

        gift = c.post('/api/products', headers=auth(admin), json={
            'sku': 'V54-GIFT-001', 'name': 'V54消费礼包', 'category': 'gift',
            'price': 600, 'stock': 5, 'description': ''
        })
        assert gift.status_code == 200, gift.text
        plogin = c.post('/api/player/auth/login', json={'username': 'v54_player', 'password': 'PlayerPass123!'})
        assert plogin.status_code == 200, plogin.text
        buy = c.post(f"/api/player/mall/purchase/{gift.json()['id']}", headers=auth(plogin.json()['access_token']), json={'quantity': 1})
        assert buy.status_code == 200, buy.text
        after_buy = c.get('/api/players', headers=auth(admin), params={'account': 'v54_player'}).json()[0]
        assert after_buy['total_recharge'] == 600
        # 商城消费不增加真实支付流水。
        agent_row2 = c.get('/api/agents', headers=auth(admin), params={'public_agent_id': agent['agent_id']}).json()[0]
        assert agent_row2['total_turnover'] == 88.0

        # 模拟从 V53 升级：历史 total_recharge 曾把真实充值也算进去；启动校准后必须只剩商城消费。
        db = SessionLocal()
        try:
            legacy = db.get(Player, player_pk)
            legacy.total_recharge = 9999
            db.commit()
        finally:
            db.close()
        sync_real_payment_aggregates()
        corrected = c.get('/api/players', headers=auth(admin), params={'account': 'v54_player'}).json()[0]
        assert corrected['total_recharge'] == 600


def test_v55_manual_coin_compensation_after_real_payment_never_duplicates_turnover_commission_or_cumulative():
    """V55：真实充值已计一次流水/分佣后，手工补偿平台币只能恢复余额，绝不二次计账。"""
    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        agent_resp = create_agent(c, admin, 'v55_agent', 'V55补偿代理', 1, 2, 0.3)
        assert agent_resp.status_code == 200, agent_resp.text
        agent = agent_resp.json()
        reg = c.post(f"/api/public/registration/{agent['agent_id']}", json={
            'username': 'v55_player', 'password': 'PlayerPass123!'
        })
        assert reg.status_code == 200, reg.text
        player_pk = reg.json()['id']

        paid = platform_payment(c, {
            'order_no': 'V55-PAID-001', 'player_id': player_pk, 'agent_id': agent['id'],
            'amount': 100, 'platform_coin': 10000, 'payment_channel': 'wechat', 'pay_status': 'paid',
        })
        assert paid.status_code == 200, paid.text

        before_dash = c.get('/api/dashboard', headers=auth(admin)).json()
        before_agent = c.get('/api/agents', headers=auth(admin), params={'public_agent_id': agent['agent_id']}).json()[0]
        before_player = c.get('/api/players', headers=auth(admin), params={'account': 'v55_player'}).json()[0]
        assert before_agent['total_turnover'] == 100.0
        assert before_player['total_recharge'] == 0

        issue = c.patch(f'/api/players/{player_pk}', headers=auth(admin), json={
            'coin_action': 'issue', 'coin_amount': 1500
        })
        assert issue.status_code == 200, issue.text

        after_dash = c.get('/api/dashboard', headers=auth(admin)).json()
        after_agent = c.get('/api/agents', headers=auth(admin), params={'public_agent_id': agent['agent_id']}).json()[0]
        after_player = c.get('/api/players', headers=auth(admin), params={'account': 'v55_player'}).json()[0]

        assert after_dash['total_turnover'] == before_dash['total_turnover']
        assert after_dash['today_turnover'] == before_dash['today_turnover']
        assert after_agent['total_turnover'] == before_agent['total_turnover']
        assert after_agent['today_turnover'] == before_agent['today_turnover']
        assert after_player['total_recharge'] == before_player['total_recharge'] == 0
        assert after_player['today_recharge'] == before_player['today_recharge'] == 100.0
        assert after_player['platform_coin_balance'] == before_player['platform_coin_balance'] + 1500

        db = SessionLocal()
        try:
            manual = (db.query(PlayerCoinLedger)
                .filter(PlayerCoinLedger.player_id == player_pk, PlayerCoinLedger.action == 'issue')
                .order_by(PlayerCoinLedger.id.desc()).first())
            assert manual is not None
            assert manual.delta == 1500
            assert '不计流水' in (manual.note or '')
        finally:
            db.close()


def test_v57_mall_order_search_and_character_snapshot():
    """V57：商城订单支持账号/商品查询，并永久记录购买时选择的角色名与区服。"""
    static_dir = Path(__file__).resolve().parent.parent / 'app' / 'static'
    center_html = (static_dir / 'player_center.html').read_text(encoding='utf-8')
    app_js = (static_dir / 'app.js').read_text(encoding='utf-8')
    assert 'id="purchaseCharacter"' in center_html
    assert '账号查询' in app_js and '商品查询' in app_js
    assert "['角色名','role_name']" in app_js
    assert "['区服','server_name']" in app_js

    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        agent_resp = create_agent(c, admin, 'v57_role_agent', 'V57角色代理', 1, 2, 0.1)
        assert agent_resp.status_code == 200, agent_resp.text
        aid = agent_resp.json()['agent_id']
        reg = c.post(f'/api/public/registration/{aid}', json={
            'username': 'v57_role_player', 'password': 'PlayerPass123!'
        })
        assert reg.status_code == 200, reg.text
        player_pk = reg.json()['id']

        db = SessionLocal()
        try:
            c1 = PlayerCharacter(player_id=player_pk, role_name='剑心', server_name='一区·龙城', is_primary=True)
            c2 = PlayerCharacter(player_id=player_pk, role_name='星河', server_name='二区·天启', is_primary=False)
            db.add_all([c1, c2]); db.commit(); db.refresh(c1); db.refresh(c2)
            char1_id, char2_id = c1.id, c2.id
        finally:
            db.close()

        issue = c.patch(f'/api/players/{player_pk}', headers=auth(admin), json={
            'coin_action': 'issue', 'coin_amount': 1000
        })
        assert issue.status_code == 200, issue.text
        gift = c.post('/api/products', headers=auth(admin), json={
            'sku': 'V57-ROLE-GIFT', 'name': 'V57角色礼包', 'category': 'gift',
            'price': 200, 'stock': 10, 'description': '角色快照测试'
        })
        assert gift.status_code == 200, gift.text

        plogin = c.post('/api/player/auth/login', json={
            'username': 'v57_role_player', 'password': 'PlayerPass123!'
        })
        assert plogin.status_code == 200, plogin.text
        pt = plogin.json()['access_token']

        # 多角色账号不传 character_id 时后端拒绝，防止订单归属到错误角色。
        missing = c.post(f"/api/player/mall/purchase/{gift.json()['id']}", headers=auth(pt), json={'quantity': 1})
        assert missing.status_code == 400
        assert '多个区服角色' in missing.json()['detail']

        buy = c.post(f"/api/player/mall/purchase/{gift.json()['id']}", headers=auth(pt), json={
            'quantity': 1, 'character_id': char2_id
        })
        assert buy.status_code == 200, buy.text
        assert buy.json()['role_name'] == '星河'
        assert buy.json()['server_name'] == '二区·天启'
        order_no = buy.json()['order_no']

        # 即使之后主角色变化，订单快照仍必须保持购买时的二区角色。
        db = SessionLocal()
        try:
            row1 = db.get(PlayerCharacter, char1_id)
            row2 = db.get(PlayerCharacter, char2_id)
            row1.is_primary = False
            row2.is_primary = True
            row2.role_name = '星河改名'
            row2.server_name = '三区·苍穹'
            db.commit()
            order = db.query(MallOrder).filter(MallOrder.order_no == order_no).one()
            assert order.role_name == '星河'
            assert order.server_name == '二区·天启'
        finally:
            db.close()

        by_account = c.get('/api/orders/mall', headers=auth(admin), params={'account': 'v57_role'})
        assert by_account.status_code == 200, by_account.text
        found = next(x for x in by_account.json() if x['order_no'] == order_no)
        assert found['player_account'] == 'v57_role_player'
        assert found['role_name'] == '星河'
        assert found['server_name'] == '二区·天启'

        by_product = c.get('/api/orders/mall', headers=auth(admin), params={'product': '角色礼包'})
        assert by_product.status_code == 200, by_product.text
        assert any(x['order_no'] == order_no for x in by_product.json())
        by_sku = c.get('/api/orders/mall', headers=auth(admin), params={'product': 'V57-ROLE-GIFT'})
        assert any(x['order_no'] == order_no for x in by_sku.json())


def test_v58_player_mall_uses_dropdown_and_auto_detail():
    html = (Path(__file__).resolve().parent.parent / 'app' / 'static' / 'player_center.html').read_text(encoding='utf-8')
    assert 'id="giftSelect"' in html
    assert '请选择礼包' in html
    assert 'id="giftDetail"' in html
    assert '礼包内容' in html
    assert '使用平台币直接购买；购买成功后自动生成商城订单' not in html
    assert 'renderGiftDetail' in html


def test_v59_player_center_feature_order_and_player_cumulative_claim():
    """V59：玩家中心一级入口固定为充值→礼包→累充→特权卡，累充可由玩家本人领取。"""
    html = (Path(__file__).resolve().parent.parent / 'app' / 'static' / 'player_center.html').read_text(encoding='utf-8')
    labels = ['平台币充值', '购买礼包', '领取累充', '特权卡']
    positions = [html.index(f'>{label}</button>') for label in labels]
    assert positions == sorted(positions)
    assert 'data-tab="recharge"' in html
    assert 'data-tab="mall"' in html
    assert 'data-tab="cumulative"' in html
    assert 'data-tab="privilege"' in html
    assert '>网页商城</button>' not in html

    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        agent_resp = create_agent(c, admin, 'v59_agent', 'V59代理', 1, 2, 0.1)
        assert agent_resp.status_code == 200, agent_resp.text
        aid = agent_resp.json()['agent_id']
        reg = c.post(f'/api/public/registration/{aid}', json={
            'username': 'v59_player', 'password': 'PlayerPass123!'
        })
        assert reg.status_code == 200, reg.text
        player_pk = reg.json()['id']
        issue = c.patch(f'/api/players/{player_pk}', headers=auth(admin), json={
            'coin_action': 'issue', 'coin_amount': 500
        })
        assert issue.status_code == 200, issue.text
        gift = c.post('/api/products', headers=auth(admin), json={
            'sku': 'V59-GIFT', 'name': 'V59礼包', 'category': 'gift',
            'price': 100, 'stock': 5, 'description': 'V59礼包内容'
        })
        assert gift.status_code == 200, gift.text
        rule = c.post('/api/recharge-rules', headers=auth(admin), json={
            'name': 'V59累充100', 'threshold_amount': 100, 'reward_content': 'V59奖励'
        })
        assert rule.status_code == 200, rule.text
        pt = c.post('/api/player/auth/login', json={
            'username': 'v59_player', 'password': 'PlayerPass123!'
        }).json()['access_token']
        buy = c.post(f"/api/player/mall/purchase/{gift.json()['id']}", headers=auth(pt), json={'quantity': 1})
        assert buy.status_code == 200, buy.text
        info = c.get('/api/player/cumulative-recharge', headers=auth(pt))
        assert info.status_code == 200, info.text
        matching = next(x for x in info.json()['rules'] if x['id'] == rule.json()['id'])
        assert info.json()['total_recharge'] == 100.0
        assert matching['eligible'] is True and matching['claimed'] is False
        claim = c.post(f"/api/player/cumulative-recharge/{rule.json()['id']}/claim", headers=auth(pt))
        assert claim.status_code == 200, claim.text
        after = c.get('/api/player/cumulative-recharge', headers=auth(pt)).json()
        matching_after = next(x for x in after['rules'] if x['id'] == rule.json()['id'])
        assert matching_after['claimed'] is True


def test_v62_player_center_removes_order_list_and_supports_cdk_redeem():
    """V62：玩家中心不展示商城订单，新增玩家本人 CDK 自助兑换。"""
    html = (Path(__file__).resolve().parent.parent / 'app' / 'static' / 'player_center.html').read_text(encoding='utf-8')
    assert '我的商城订单' not in html
    assert 'id="orders"' not in html
    assert 'loadOrders()' not in html
    assert 'data-tab="cdk"' in html
    assert '>CDK兑换</button>' in html
    assert 'id="cdkCode"' in html
    assert '/api/player/cdk/redeem' in html

    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        batch = c.post('/api/redemption-batches', headers=auth(admin), json={'name': 'V62玩家兑换批次'})
        assert batch.status_code == 200, batch.text
        generated = c.post(
            f"/api/redemption-batches/{batch.json()['id']}/generate",
            headers=auth(admin),
            json={'count': 1, 'prefix': 'V62'},
        )
        assert generated.status_code == 200, generated.text
        code = generated.json()['codes'][0]

        agent_resp = create_agent(c, admin, 'v62_agent', 'V62代理', 1, 1, 0.1)
        assert agent_resp.status_code == 200, agent_resp.text
        reg = c.post(f"/api/public/registration/{agent_resp.json()['agent_id']}", json={
            'username': 'v62_cdk_player', 'password': 'PlayerPass123!'
        })
        assert reg.status_code == 200, reg.text
        pt = c.post('/api/player/auth/login', json={
            'username': 'v62_cdk_player', 'password': 'PlayerPass123!'
        }).json()['access_token']
        db = SessionLocal()
        try:
            player = db.query(Player).filter(Player.username == 'v62_cdk_player').first()
            character = PlayerCharacter(
                player_id=player.id,
                role_name='V62剑心',
                server_name='V62一区',
                is_primary=True,
            )
            db.add(character); db.commit(); db.refresh(character)
            character_id = character.id
        finally:
            db.close()

        missing_character = c.post('/api/player/cdk/redeem', headers=auth(pt), json={'code': code.lower()})
        assert missing_character.status_code == 422

        redeem = c.post('/api/player/cdk/redeem', headers=auth(pt), json={'code': code.lower(), 'character_id': character_id})
        assert redeem.status_code == 200, redeem.text
        assert redeem.json()['message'] == 'CDK兑换成功'
        assert redeem.json()['cdk_name'] == 'V62玩家兑换批次'
        assert redeem.json()['role_name'] == 'V62剑心'
        assert redeem.json()['server_name'] == 'V62一区'

        again = c.post('/api/player/cdk/redeem', headers=auth(pt), json={'code': code, 'character_id': character_id})
        assert again.status_code == 409

        rows = c.get('/api/redemption-batches', headers=auth(admin))
        current = next(x for x in rows.json() if x['id'] == batch.json()['id'])
        assert current['redeemed_count'] == 1
        assert current['unused_count'] == 0


def test_v64_cumulative_claim_selects_character_then_only_shows_claimable_rewards():
    """V64：玩家先选角色/区服，再获取该角色可领取累充；顶部不再展示当日/永久累充。"""
    html = (Path(__file__).resolve().parent.parent / 'app' / 'static' / 'player_center.html').read_text(encoding='utf-8')
    assert 'id="cumulativeCharacter"' in html
    assert '领取角色 / 区服' in html
    assert 'claimable_only=true' in html
    assert 'id="permanentCumulative"' not in html
    assert 'id="todayCumulative"' not in html
    assert '请先选择角色 / 区服' in html
    assert '该角色当前暂无可领取的累充奖励' in html
    assert '选择可领取奖励' in html

    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        agent_resp = create_agent(c, admin, 'v64_agent', 'V64代理', 1, 1, 0.1)
        assert agent_resp.status_code == 200, agent_resp.text
        reg = c.post(f"/api/public/registration/{agent_resp.json()['agent_id']}", json={
            'username': 'v64_player', 'password': 'PlayerPass123!'
        })
        assert reg.status_code == 200, reg.text
        player_pk = reg.json()['id']

        db = SessionLocal()
        try:
            c1 = PlayerCharacter(player_id=player_pk, role_name='剑一', server_name='一区', is_primary=True)
            c2 = PlayerCharacter(player_id=player_pk, role_name='剑二', server_name='二区', is_primary=False)
            db.add_all([c1, c2]); db.commit(); db.refresh(c1); db.refresh(c2)
            char1_id, char2_id = c1.id, c2.id
        finally:
            db.close()

        issue = c.patch(f'/api/players/{player_pk}', headers=auth(admin), json={
            'coin_action': 'issue', 'coin_amount': 1000
        })
        assert issue.status_code == 200, issue.text
        gift = c.post('/api/products', headers=auth(admin), json={
            'sku': 'V64-GIFT', 'name': 'V64礼包', 'category': 'gift',
            'price': 200, 'stock': 10, 'description': '元宝 × 1000\n强化石 × 20'
        })
        assert gift.status_code == 200, gift.text
        rule = c.post('/api/recharge-rules', headers=auth(admin), json={
            'name': 'V64累充200', 'threshold_amount': 200,
            'reward_content': '钻石 × 50\n宝箱 × 1'
        })
        assert rule.status_code == 200, rule.text
        rule_id = rule.json()['id']

        pt = c.post('/api/player/auth/login', json={
            'username': 'v64_player', 'password': 'PlayerPass123!'
        }).json()['access_token']

        # 二区角色先消费 200 平台币，只有二区角色达到领取条件。
        buy2 = c.post(f"/api/player/mall/purchase/{gift.json()['id']}", headers=auth(pt), json={
            'quantity': 1, 'character_id': char2_id
        })
        assert buy2.status_code == 200, buy2.text

        char1_rewards = c.get('/api/player/cumulative-recharge', headers=auth(pt), params={
            'character_id': char1_id, 'claimable_only': 'true'
        })
        assert char1_rewards.status_code == 200, char1_rewards.text
        assert char1_rewards.json()['role_name'] == '剑一'
        assert char1_rewards.json()['server_name'] == '一区'
        assert char1_rewards.json()['rules'] == []

        char2_rewards = c.get('/api/player/cumulative-recharge', headers=auth(pt), params={
            'character_id': char2_id, 'claimable_only': 'true'
        })
        assert char2_rewards.status_code == 200, char2_rewards.text
        assert char2_rewards.json()['role_name'] == '剑二'
        assert char2_rewards.json()['server_name'] == '二区'
        ids2 = [x['id'] for x in char2_rewards.json()['rules']]
        assert rule_id in ids2
        own_rule2 = next(x for x in char2_rewards.json()['rules'] if x['id'] == rule_id)
        assert own_rule2['eligible'] is True
        assert own_rule2['claimed'] is False

        claim2 = c.post(f'/api/player/cumulative-recharge/{rule_id}/claim', headers=auth(pt), params={
            'character_id': char2_id
        })
        assert claim2.status_code == 200, claim2.text
        assert claim2.json()['role_name'] == '剑二'
        assert claim2.json()['server_name'] == '二区'

        # 已领取奖励不会再出现在该角色“可领取奖励”下拉框。
        char2_after = c.get('/api/player/cumulative-recharge', headers=auth(pt), params={
            'character_id': char2_id, 'claimable_only': 'true'
        })
        assert char2_after.status_code == 200, char2_after.text
        assert rule_id not in [x['id'] for x in char2_after.json()['rules']]

        # 同账号另一个区服角色独立累计、独立领取同一档奖励。
        buy1 = c.post(f"/api/player/mall/purchase/{gift.json()['id']}", headers=auth(pt), json={
            'quantity': 1, 'character_id': char1_id
        })
        assert buy1.status_code == 200, buy1.text
        char1_after_buy = c.get('/api/player/cumulative-recharge', headers=auth(pt), params={
            'character_id': char1_id, 'claimable_only': 'true'
        })
        assert rule_id in [x['id'] for x in char1_after_buy.json()['rules']]
        claim1 = c.post(f'/api/player/cumulative-recharge/{rule_id}/claim', headers=auth(pt), params={
            'character_id': char1_id
        })
        assert claim1.status_code == 200, claim1.text
        assert claim1.json()['role_name'] == '剑一'


def test_v65_selected_character_shows_today_and_permanent_cumulative_before_rewards():
    """V65：领取累充选定角色后，先显示该角色当日/永久累充，再展示可领取奖励。"""
    html = (Path(__file__).resolve().parent.parent / 'app' / 'static' / 'player_center.html').read_text(encoding='utf-8')
    assert 'cumulative-stats' in html
    assert '<span>当日累充</span>' in html
    assert '<span>永久累充</span>' in html
    assert 'cumulativeData.today_recharge' in html
    assert 'cumulativeData.total_recharge' in html

    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        agent_resp = create_agent(c, admin, 'v65_agent', 'V65代理', 1, 1, 0.1)
        assert agent_resp.status_code == 200, agent_resp.text
        reg = c.post(f"/api/public/registration/{agent_resp.json()['agent_id']}", json={
            'username': 'v65_player', 'password': 'PlayerPass123!'
        })
        assert reg.status_code == 200, reg.text
        player_pk = reg.json()['id']

        db = SessionLocal()
        try:
            c1 = PlayerCharacter(player_id=player_pk, role_name='今日剑', server_name='一区', is_primary=True)
            c2 = PlayerCharacter(player_id=player_pk, role_name='永久剑', server_name='二区', is_primary=False)
            db.add_all([c1, c2]); db.commit(); db.refresh(c1); db.refresh(c2)
            char1_id, char2_id = c1.id, c2.id
        finally:
            db.close()

        issue = c.patch(f'/api/players/{player_pk}', headers=auth(admin), json={
            'coin_action': 'issue', 'coin_amount': 1000
        })
        assert issue.status_code == 200, issue.text
        gift = c.post('/api/products', headers=auth(admin), json={
            'sku': 'V65-GIFT', 'name': 'V65礼包', 'category': 'gift',
            'price': 300, 'stock': 10, 'description': '元宝 × 300'
        })
        assert gift.status_code == 200, gift.text
        pt = c.post('/api/player/auth/login', json={
            'username': 'v65_player', 'password': 'PlayerPass123!'
        }).json()['access_token']

        buy = c.post(f"/api/player/mall/purchase/{gift.json()['id']}", headers=auth(pt), json={
            'quantity': 1, 'character_id': char1_id
        })
        assert buy.status_code == 200, buy.text

        char1 = c.get('/api/player/cumulative-recharge', headers=auth(pt), params={
            'character_id': char1_id, 'claimable_only': 'true'
        })
        assert char1.status_code == 200, char1.text
        assert char1.json()['today_recharge'] == 300.0
        assert char1.json()['total_recharge'] == 300.0

        char2 = c.get('/api/player/cumulative-recharge', headers=auth(pt), params={
            'character_id': char2_id, 'claimable_only': 'true'
        })
        assert char2.status_code == 200, char2.text
        assert char2.json()['today_recharge'] == 0.0
        assert char2.json()['total_recharge'] == 0.0



def test_v67_cdk_redeem_requires_owned_character_and_saves_role_server_snapshot():
    """V67：CDK 兑换必须先选当前玩家自己的角色/区服，并固化兑换时角色快照。"""
    html = (Path(__file__).resolve().parent.parent / 'app' / 'static' / 'player_center.html').read_text(encoding='utf-8')
    assert 'id="cdkCharacter"' in html
    assert '请选择兑换角色 / 区服' in html
    assert 'character_id:selectedCDKCharacterId' in html

    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        batch = c.post('/api/redemption-batches', headers=auth(admin), json={'name': 'V67角色CDK批次'})
        assert batch.status_code == 200, batch.text
        generated = c.post(
            f"/api/redemption-batches/{batch.json()['id']}/generate",
            headers=auth(admin),
            json={'count': 2, 'prefix': 'V67'},
        )
        assert generated.status_code == 200, generated.text
        code1, code2 = generated.json()['codes']

        agent = create_agent(c, admin, 'v67_agent', 'V67代理', 1, 1, 0.1)
        assert agent.status_code == 200, agent.text
        for username in ['v67_owner', 'v67_other']:
            reg = c.post(f"/api/public/registration/{agent.json()['agent_id']}", json={
                'username': username, 'password': 'PlayerPass123!'
            })
            assert reg.status_code == 200, reg.text

        db = SessionLocal()
        try:
            owner = db.query(Player).filter(Player.username == 'v67_owner').first()
            other = db.query(Player).filter(Player.username == 'v67_other').first()
            owner_char = PlayerCharacter(player_id=owner.id, role_name='星河', server_name='二区·天启', is_primary=True)
            other_char = PlayerCharacter(player_id=other.id, role_name='夜雨', server_name='三区·苍穹', is_primary=True)
            db.add_all([owner_char, other_char]); db.commit(); db.refresh(owner_char); db.refresh(other_char)
            owner_id = owner.id
            owner_char_id, other_char_id = owner_char.id, other_char.id
        finally:
            db.close()

        token = c.post('/api/player/auth/login', json={
            'username': 'v67_owner', 'password': 'PlayerPass123!'
        }).json()['access_token']

        foreign = c.post('/api/player/cdk/redeem', headers=auth(token), json={
            'code': code1, 'character_id': other_char_id
        })
        assert foreign.status_code == 404

        ok = c.post('/api/player/cdk/redeem', headers=auth(token), json={
            'code': code1, 'character_id': owner_char_id
        })
        assert ok.status_code == 200, ok.text
        assert ok.json()['character_id'] == owner_char_id
        assert ok.json()['role_name'] == '星河'
        assert ok.json()['server_name'] == '二区·天启'

        db = SessionLocal()
        try:
            row = db.query(RedemptionCode).filter(RedemptionCode.code == code1).first()
            assert row.player_id == owner_id
            assert row.character_id == owner_char_id
            assert row.role_name == '星河'
            assert row.server_name == '二区·天启'
        finally:
            db.close()

        no_character = c.post('/api/player/cdk/redeem', headers=auth(token), json={'code': code2})
        assert no_character.status_code == 422


def test_v68_login_pages_do_not_flash_or_ship_default_credentials():
    static_dir = Path(__file__).resolve().parents[1] / "app" / "static"
    index = (static_dir / "index.html").read_text(encoding="utf-8")
    app_js = (static_dir / "app.js").read_text(encoding="utf-8")
    player = (static_dir / "player_center.html").read_text(encoding="utf-8")

    assert 'id="login" class="login-page hidden"' in index
    assert 'id="loginUser" value="admin"' not in index
    assert 'value="ChangeMe123!"' not in index
    assert 'placeholder="请输入登录账号"' in index
    assert "if(token) showApp().catch(()=>showLogin());" in app_js
    assert "else showLogin();" in app_js
    assert 'id="loginView" class="login-wrap hidden"' in player
    assert "if(token)enterCenter();else $('#loginView').classList.remove('hidden');" in player



def test_v71_privilege_card_week_month_year_and_daily_claim_are_character_scoped():
    """V71：特权卡按角色购买，每天仅领取一次；续购顺延，不污染累充/流水口径。"""
    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        agent = create_agent(c, admin, 'v71_agent', 'V71代理', 1, 1, 0.1)
        assert agent.status_code == 200, agent.text
        reg = c.post(f"/api/public/registration/{agent.json()['agent_id']}", json={
            'username': 'v71_player', 'password': 'PlayerPass123!'
        })
        assert reg.status_code == 200, reg.text
        player_pk = reg.json()['id']
        db = SessionLocal()
        try:
            char = PlayerCharacter(player_id=player_pk, role_name='周卡剑', server_name='一区', is_primary=True)
            db.add(char); db.commit(); db.refresh(char); char_id = char.id
        finally:
            db.close()

        issue = c.patch(f'/api/players/{player_pk}', headers=auth(admin), json={'coin_action':'issue','coin_amount':500})
        assert issue.status_code == 200, issue.text
        card = c.post('/api/privilege-cards', headers=auth(admin), json={
            'name':'V71周卡', 'card_type':'week', 'price_coins':50,
            'daily_reward_content':'元宝 × 100\n强化石 × 2', 'enabled':True,
        })
        assert card.status_code == 200, card.text
        card_id = card.json()['id']
        token = c.post('/api/player/auth/login', json={'username':'v71_player','password':'PlayerPass123!'}).json()['access_token']

        listing = c.get('/api/player/privilege', headers=auth(token), params={'character_id':char_id})
        assert listing.status_code == 200, listing.text
        rule = next(x for x in listing.json()['cards'] if x['id'] == card_id)
        assert rule['duration_days'] == 7
        assert rule['price_coins'] == 50

        purchase = c.post(f'/api/player/privilege/{card_id}/purchase', headers=auth(token), json={'character_id':char_id})
        assert purchase.status_code == 200, purchase.text
        data = purchase.json()['purchase']
        assert data['role_name'] == '周卡剑'
        assert data['server_name'] == '一区'
        assert data['start_date'] == str(business_today())
        assert data['end_date'] == str(business_today() + timedelta(days=6))
        assert purchase.json()['platform_coin_balance'] == 450

        # 特权卡消费不是礼包商城订单，因此不增加角色累充。
        cumulative = c.get('/api/player/cumulative-recharge', headers=auth(token), params={'character_id':char_id})
        assert cumulative.status_code == 200
        assert cumulative.json()['total_recharge'] == 0.0

        claim = c.post(f"/api/player/privilege/purchases/{data['id']}/claim", headers=auth(token))
        assert claim.status_code == 200, claim.text
        assert claim.json()['reward_content'] == '元宝 × 100\n强化石 × 2'
        duplicate = c.post(f"/api/player/privilege/purchases/{data['id']}/claim", headers=auth(token))
        assert duplicate.status_code == 409

        # 同卡续购从上一周期结束后的下一天生效，避免同日两份同卡奖励。
        renew = c.post(f'/api/player/privilege/{card_id}/purchase', headers=auth(token), json={'character_id':char_id})
        assert renew.status_code == 200, renew.text
        renewed = renew.json()['purchase']
        assert renewed['start_date'] == str(business_today() + timedelta(days=7))
        assert renewed['end_date'] == str(business_today() + timedelta(days=13))
        assert renewed['status'] == 'waiting'
        wait_claim = c.post(f"/api/player/privilege/purchases/{renewed['id']}/claim", headers=auth(token))
        assert wait_claim.status_code == 400

        db = SessionLocal()
        try:
            assert db.query(PrivilegeCardPurchase).filter(PrivilegeCardPurchase.player_id == player_pk).count() == 2
            assert db.query(PrivilegeCardClaim).filter(PrivilegeCardClaim.player_id == player_pk).count() == 1
        finally:
            db.close()


def test_v71_superadmin_real_player_behavior_test_reuses_business_paths():
    """V71：超管可按账号/区服/角色真实模拟礼包、特权卡、累充领取。"""
    static_dir = Path(__file__).resolve().parents[1] / 'app' / 'static'
    index = (static_dir / 'index.html').read_text(encoding='utf-8')
    app_js = (static_dir / 'app.js').read_text(encoding='utf-8')
    player_html = (static_dir / 'player_center.html').read_text(encoding='utf-8')
    assert 'data-view="playerBehaviorTest"' in index
    assert 'data-view="privilegeCards"' in index
    assert '/api/player-behavior-test/mall-purchase' in app_js
    assert '/api/player-behavior-test/privilege-purchase' in app_js
    assert '/api/player-behavior-test/cumulative-claim' in app_js
    assert 'id="privilegeCharacter"' in player_html
    assert '领取今日奖励' in player_html

    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        agent = create_agent(c, admin, 'v71_test_agent', 'V71测试代理', 1, 1, 0.1)
        reg = c.post(f"/api/public/registration/{agent.json()['agent_id']}", json={
            'username':'v71_behavior', 'password':'PlayerPass123!'
        })
        player_pk = reg.json()['id']
        db = SessionLocal()
        try:
            char = PlayerCharacter(player_id=player_pk, role_name='测试战神', server_name='九区', is_primary=True)
            db.add(char); db.commit(); db.refresh(char); char_id = char.id
        finally:
            db.close()
        c.patch(f'/api/players/{player_pk}', headers=auth(admin), json={'coin_action':'issue','coin_amount':1000})
        gift = c.post('/api/products', headers=auth(admin), json={
            'sku':'V71-BEH-GIFT','name':'行为测试礼包','category':'gift','price':200,'stock':5,'description':'元宝 × 200'
        })
        rule = c.post('/api/recharge-rules', headers=auth(admin), json={
            'name':'V71行为累充200','threshold_amount':200,'reward_content':'宝箱 × 1'
        })
        card = c.post('/api/privilege-cards', headers=auth(admin), json={
            'name':'V71行为月卡','card_type':'month','price_coins':60,'daily_reward_content':'钻石 × 10','enabled':True
        })
        found = c.get('/api/player-behavior-test/characters', headers=auth(admin), params={'keyword':'测试战神'})
        assert found.status_code == 200, found.text
        assert any(x['character_id'] == char_id and x['server_name'] == '九区' for x in found.json())

        buy = c.post('/api/player-behavior-test/mall-purchase', headers=auth(admin), json={'character_id':char_id,'product_id':gift.json()['id']})
        assert buy.status_code == 200, buy.text
        cumulative = c.get('/api/player-behavior-test/cumulative', headers=auth(admin), params={'character_id':char_id})
        assert cumulative.status_code == 200, cumulative.text
        assert cumulative.json()['total_recharge'] == 200.0
        assert rule.json()['id'] in [x['id'] for x in cumulative.json()['rules']]

        claimed = c.post('/api/player-behavior-test/cumulative-claim', headers=auth(admin), json={'character_id':char_id,'rule_id':rule.json()['id']})
        assert claimed.status_code == 200, claimed.text
        card_buy = c.post('/api/player-behavior-test/privilege-purchase', headers=auth(admin), json={'character_id':char_id,'card_id':card.json()['id']})
        assert card_buy.status_code == 200, card_buy.text
        assert card_buy.json()['purchase']['duration_days'] == 30


def test_v72_behavior_character_search_returns_clear_result_and_unbound_message_data():
    """V72：行为测试搜索必须返回可选角色；账号存在但未绑定角色时要可识别。"""
    static_dir = Path(__file__).resolve().parents[1] / 'app' / 'static'
    app_js = (static_dir / 'app.js').read_text(encoding='utf-8')
    assert '/api/player-behavior-test/character-search' in app_js
    assert '找到 ${playerBehaviorTestState.characters.length} 个角色' in app_js
    assert '尚未绑定角色 / 区服' in app_js

    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        agent = create_agent(c, admin, 'v72_search_agent', 'V72搜索代理', 1, 1, 0.1)
        reg = c.post(f"/api/public/registration/{agent.json()['agent_id']}", json={
            'username':'v72_unbound', 'password':'PlayerPass123!'
        })
        assert reg.status_code == 200, reg.text
        unbound = c.get('/api/player-behavior-test/character-search', headers=auth(admin), params={'keyword':'v72_unbound'})
        assert unbound.status_code == 200, unbound.text
        assert unbound.json()['items'] == []
        assert unbound.json()['unbound_players'][0]['username'] == 'v72_unbound'

        player_pk = reg.json()['id']
        db = SessionLocal()
        try:
            char = PlayerCharacter(player_id=player_pk, role_name='V72战士', server_name='V72一区', is_primary=True)
            db.add(char); db.commit(); db.refresh(char); char_id = char.id
        finally:
            db.close()
        found = c.get('/api/player-behavior-test/character-search', headers=auth(admin), params={'keyword':'V72战士'})
        assert found.status_code == 200, found.text
        assert found.json()['count'] == 1
        assert found.json()['items'][0]['character_id'] == char_id
        assert found.json()['items'][0]['server_name'] == 'V72一区'


def test_v73_behavior_search_button_is_clickable_and_static_cache_busted():
    static_dir = Path(__file__).resolve().parents[1] / 'app' / 'static'
    app_js = (static_dir / 'app.js').read_text(encoding='utf-8')
    index_html = (static_dir / 'index.html').read_text(encoding='utf-8')
    assert 'id="behaviorSearchBtn"' in app_js
    assert 'type="button" class="btn" id="behaviorSearchBtn"' in app_js
    assert "searchBtn.addEventListener('click'" in app_js
    assert "e.preventDefault();e.stopPropagation();runSearch()" in app_js
    assert '/api/player-behavior-test/character-search?' in app_js
    assert '/static/app.js?v=v91-item-import-gift-limits' in index_html


def test_v74_system_settings_profile_password_and_superadmin_management():
    """V74：超管后台新增系统设置，可自助改密并新增超级管理员。"""
    static_dir = Path(__file__).resolve().parents[1] / 'app' / 'static'
    index_html = (static_dir / 'index.html').read_text(encoding='utf-8')
    app_js = (static_dir / 'app.js').read_text(encoding='utf-8')
    assert 'data-section="system"' in index_html
    assert 'data-view="profileSettings"' in index_html
    assert 'data-view="adminManagers"' in index_html
    assert '/api/system/profile/password' in app_js
    assert '/api/system/admins' in app_js

    with TestClient(app) as c:
        root_token = login(c, 'admin', 'ChangeMe123!')
        me = c.get('/api/auth/me', headers=auth(root_token))
        assert me.status_code == 200
        assert 'system.settings' in me.json()['permissions']
        assert 'system.admins.manage' in me.json()['permissions']

        profile = c.get('/api/system/profile', headers=auth(root_token))
        assert profile.status_code == 200, profile.text
        assert profile.json()['username'] == 'admin'
        assert profile.json()['role_name'] == '超级管理员'

        created = c.post('/api/system/admins', headers=auth(root_token), json={
            'username': 'v74_superadmin',
            'password': 'SuperPass123!',
        })
        assert created.status_code == 200, created.text
        assert created.json()['role'] == 'superadmin'
        assert created.json()['enabled'] is True

        duplicate = c.post('/api/system/admins', headers=auth(root_token), json={
            'username': 'V74_SUPERADMIN',
            'password': 'AnotherPass123!',
        })
        assert duplicate.status_code == 409

        new_token = login(c, 'v74_superadmin', 'SuperPass123!')
        new_me = c.get('/api/auth/me', headers=auth(new_token))
        assert new_me.status_code == 200
        assert new_me.json()['role'] == 'superadmin'
        assert 'system.admins.manage' in new_me.json()['permissions']
        admins = c.get('/api/system/admins', headers=auth(new_token))
        assert admins.status_code == 200, admins.text
        assert any(x['username'] == 'v74_superadmin' for x in admins.json())

        wrong = c.patch('/api/system/profile/password', headers=auth(new_token), json={
            'current_password': 'WrongPass123!',
            'new_password': 'NewSuperPass456!',
            'confirm_password': 'NewSuperPass456!',
        })
        assert wrong.status_code == 400

        mismatch = c.patch('/api/system/profile/password', headers=auth(new_token), json={
            'current_password': 'SuperPass123!',
            'new_password': 'NewSuperPass456!',
            'confirm_password': 'DifferentPass456!',
        })
        assert mismatch.status_code == 400

        changed = c.patch('/api/system/profile/password', headers=auth(new_token), json={
            'current_password': 'SuperPass123!',
            'new_password': 'NewSuperPass456!',
            'confirm_password': 'NewSuperPass456!',
        })
        assert changed.status_code == 200, changed.text
        assert changed.json()['message'] == '密码修改成功'
        assert c.post('/api/auth/login', json={'username':'v74_superadmin','password':'SuperPass123!'}).status_code == 401
        assert c.post('/api/auth/login', json={'username':'v74_superadmin','password':'NewSuperPass456!'}).status_code == 200

        agent = create_agent(c, root_token, 'v74_agent', 'V74普通代理', 1, 1, 0.1)
        assert agent.status_code == 200, agent.text
        agent_token = login(c, 'v74_agent', 'AgentPass123!')
        forbidden = c.get('/api/system/admins', headers=auth(agent_token))
        assert forbidden.status_code == 403


def test_v75_agents_get_profile_only_and_superadmin_can_edit_system_names():
    """V75：代理可使用个人信息改密；管理员/系统编辑仅超管可见和可调用。"""
    static_dir = Path(__file__).resolve().parents[1] / 'app' / 'static'
    index_html = (static_dir / 'index.html').read_text(encoding='utf-8')
    app_js = (static_dir / 'app.js').read_text(encoding='utf-8')
    player_html = (static_dir / 'player_center.html').read_text(encoding='utf-8')
    assert 'data-view="systemEditor"' in index_html
    assert 'data-permission="system.branding.manage"' in index_html
    assert 'id="sidebarBrandName"' in index_html
    assert '/api/system/branding' in app_js
    assert '/api/public/system-branding' in app_js
    assert 'id="loginCenterName"' in player_html
    assert 'id="topbarCenterName"' in player_html
    assert '/api/public/system-branding' in player_html

    with TestClient(app) as c:
        root_token = login(c, 'admin', 'ChangeMe123!')
        root_me = c.get('/api/auth/me', headers=auth(root_token))
        assert root_me.status_code == 200
        assert 'system.settings' in root_me.json()['permissions']
        assert 'system.admins.manage' in root_me.json()['permissions']
        assert 'system.branding.manage' in root_me.json()['permissions']

        defaults = c.get('/api/public/system-branding')
        assert defaults.status_code == 200
        assert defaults.json()['backend_name']
        assert defaults.json()['player_center_name']

        created = create_agent(c, root_token, 'v75_profile_agent', 'V75个人信息代理', 1, 1, 0.1)
        assert created.status_code == 200, created.text
        agent_token = login(c, 'v75_profile_agent', 'AgentPass123!')
        agent_me = c.get('/api/auth/me', headers=auth(agent_token))
        assert agent_me.status_code == 200
        perms = agent_me.json()['permissions']
        assert 'system.settings' in perms
        assert 'system.admins.manage' not in perms
        assert 'system.branding.manage' not in perms

        profile = c.get('/api/system/profile', headers=auth(agent_token))
        assert profile.status_code == 200, profile.text
        assert profile.json()['username'] == 'v75_profile_agent'
        assert profile.json()['role_name'] == '一级代理'

        forbidden_admins = c.get('/api/system/admins', headers=auth(agent_token))
        forbidden_branding = c.get('/api/system/branding', headers=auth(agent_token))
        forbidden_update = c.patch('/api/system/branding', headers=auth(agent_token), json={
            'backend_name': '不允许代理修改', 'player_center_name': '不允许代理修改'
        })
        assert forbidden_admins.status_code == 403
        assert forbidden_branding.status_code == 403
        assert forbidden_update.status_code == 403

        changed_password = c.patch('/api/system/profile/password', headers=auth(agent_token), json={
            'current_password': 'AgentPass123!',
            'new_password': 'AgentPass456!',
            'confirm_password': 'AgentPass456!',
        })
        assert changed_password.status_code == 200, changed_password.text
        assert c.post('/api/auth/login', json={'username':'v75_profile_agent','password':'AgentPass123!'}).status_code == 401
        assert c.post('/api/auth/login', json={'username':'v75_profile_agent','password':'AgentPass456!'}).status_code == 200

        updated = c.patch('/api/system/branding', headers=auth(root_token), json={
            'backend_name': '运营管理后台',
            'player_center_name': '游戏会员中心',
        })
        assert updated.status_code == 200, updated.text
        assert updated.json()['backend_name'] == '运营管理后台'
        assert updated.json()['player_center_name'] == '游戏会员中心'
        public = c.get('/api/public/system-branding')
        assert public.status_code == 200
        assert public.json()['backend_name'] == '运营管理后台'
        assert public.json()['player_center_name'] == '游戏会员中心'



def clear_ip_security_tables():
    db = SessionLocal()
    try:
        db.query(AdminIPWhitelist).delete(synchronize_session=False)
        db.query(AdminLoginIPState).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def test_v76_ip_whitelist_blocks_admin_backend_but_not_player_center():
    clear_ip_security_tables()
    try:
        with TestClient(app) as c:
            admin_ip = '203.0.113.10'
            other_ip = '203.0.113.11'
            token = login_with_ip = c.post(
                '/api/auth/login',
                headers={'X-Forwarded-For': admin_ip},
                json={'username': 'admin', 'password': 'ChangeMe123!'},
            )
            assert login_with_ip.status_code == 200, login_with_ip.text
            token = login_with_ip.json()['access_token']

            before = c.get('/api/system/ip-access', headers={**auth(token), 'X-Forwarded-For': admin_ip})
            assert before.status_code == 200, before.text
            assert before.json()['whitelist_enabled'] is False
            assert before.json()['current_ip'] == admin_ip

            add = c.post('/api/system/ip-access/whitelist', headers={**auth(token), 'X-Forwarded-For': admin_ip}, json={
                'ip_address': admin_ip,
                'note': '测试当前IP',
            })
            assert add.status_code == 200, add.text
            assert add.json()['whitelist_enabled'] is True
            assert len(add.json()['whitelist']) == 1

            same_ip_root = c.get('/', headers={'X-Forwarded-For': admin_ip})
            assert same_ip_root.status_code == 200
            denied_root = c.get('/', headers={'X-Forwarded-For': other_ip})
            assert denied_root.status_code == 403
            assert other_ip in denied_root.text

            denied_api = c.get('/api/dashboard', headers={**auth(token), 'X-Forwarded-For': other_ip})
            assert denied_api.status_code == 403
            assert '未加入后台访问白名单' in denied_api.json()['detail']

            # 玩家中心和公开接口不属于后台白名单限制范围。
            player_page = c.get('/player', headers={'X-Forwarded-For': other_ip})
            assert player_page.status_code == 200
            branding = c.get('/api/public/system-branding', headers={'X-Forwarded-For': other_ip})
            assert branding.status_code == 200

            # 最后一条白名单不能直接删除，防止误操作后关闭IP访问保护。
            row_id = add.json()['whitelist'][0]['id']
            last_delete = c.delete(f'/api/system/ip-access/whitelist/{row_id}', headers={**auth(token), 'X-Forwarded-For': admin_ip})
            assert last_delete.status_code == 400
            assert '不能删除最后一个白名单IP' in last_delete.json()['detail']
    finally:
        clear_ip_security_tables()


def test_v76_frequent_backend_login_failures_are_blacklisted_and_can_be_removed():
    clear_ip_security_tables()
    try:
        with TestClient(app) as c:
            attacker_ip = '198.51.100.55'
            admin_ip = '198.51.100.10'
            last = None
            for _ in range(8):
                last = c.post('/api/auth/login', headers={'X-Forwarded-For': attacker_ip}, json={
                    'username': 'admin', 'password': 'WrongPassword123!'
                })
            assert last is not None
            assert last.status_code == 403, last.text
            assert '已被拉黑' in last.json()['detail']

            blocked_correct = c.post('/api/auth/login', headers={'X-Forwarded-For': attacker_ip}, json={
                'username': 'admin', 'password': 'ChangeMe123!'
            })
            assert blocked_correct.status_code == 403
            assert '拉黑' in blocked_correct.json()['detail']

            admin_login = c.post('/api/auth/login', headers={'X-Forwarded-For': admin_ip}, json={
                'username': 'admin', 'password': 'ChangeMe123!'
            })
            assert admin_login.status_code == 200, admin_login.text
            token = admin_login.json()['access_token']
            settings = c.get('/api/system/ip-access', headers={**auth(token), 'X-Forwarded-For': admin_ip})
            assert settings.status_code == 200
            black = next(x for x in settings.json()['blacklist'] if x['ip_address'] == attacker_ip)
            assert black['failure_count'] >= 8

            unblocked = c.delete(f"/api/system/ip-access/blacklist/{black['id']}", headers={**auth(token), 'X-Forwarded-For': admin_ip})
            assert unblocked.status_code == 200, unblocked.text
            assert all(x['ip_address'] != attacker_ip for x in unblocked.json()['blacklist'])

            login_again = c.post('/api/auth/login', headers={'X-Forwarded-For': attacker_ip}, json={
                'username': 'admin', 'password': 'ChangeMe123!'
            })
            assert login_again.status_code == 200, login_again.text
    finally:
        clear_ip_security_tables()


def test_v76_ip_access_management_is_superadmin_only():
    clear_ip_security_tables()
    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        created = create_agent(c, admin, 'v76_ip_agent', 'V76普通代理', 1, 1)
        assert created.status_code == 200, created.text
        agent_token = login(c, 'v76_ip_agent', 'AgentPass123!')
        denied = c.get('/api/system/ip-access', headers=auth(agent_token))
        assert denied.status_code == 403
        me = c.get('/api/auth/me', headers=auth(agent_token))
        assert me.status_code == 200
        assert 'system.settings' in me.json()['permissions']
        assert 'system.ip_access.manage' not in me.json()['permissions']


def test_v77_legacy_admin_still_gets_system_settings_and_static_is_no_cache():
    from app.models import AdminUser
    from app.security import hash_password
    db = SessionLocal()
    try:
        row = db.query(AdminUser).filter(AdminUser.username == 'legacy_admin_v77').first()
        if not row:
            row = AdminUser(username='legacy_admin_v77', password_hash=hash_password('LegacyPass123!'), role='admin', enabled=True)
            db.add(row)
        else:
            row.role = 'admin'
            row.password_hash = hash_password('LegacyPass123!')
            row.enabled = True
        db.commit()
    finally:
        db.close()

    with TestClient(app) as c:
        r = c.post('/api/auth/login', json={'username':'legacy_admin_v77','password':'LegacyPass123!'})
        assert r.status_code == 200, r.text
        data = r.json()
        assert 'system.settings' in data['permissions']
        assert 'system.admins.manage' in data['permissions']
        assert 'system.branding.manage' in data['permissions']
        assert 'system.ip_access.manage' in data['permissions']
        token = data['access_token']
        assert c.get('/api/system/profile', headers=auth(token)).status_code == 200
        assert c.get('/api/system/ip-access', headers=auth(token)).status_code == 200

        index = c.get('/')
        assert index.status_code == 200
        assert 'no-store' in index.headers.get('cache-control','')
        assert index.headers.get('x-cps-build') == 'v91-item-import-gift-limits'
        assert '系统设置' in index.text
        assert '个人信息' in index.text
        assert '管理员' in index.text
        assert '系统编辑' in index.text
        assert '白名单' in index.text
        js = c.get('/static/app.js?v=v91-item-import-gift-limits')
        assert js.status_code == 200
        assert 'no-store' in js.headers.get('cache-control','')



def test_v78_brand_logo_picker_is_bundled_selectable_and_persistent():
    """V78：内置品牌图标可预览选择并持久化，非法图标不能写入。"""
    static_dir = Path(__file__).resolve().parents[1] / 'app' / 'static'
    index_html = (static_dir / 'index.html').read_text(encoding='utf-8')
    app_js = (static_dir / 'app.js').read_text(encoding='utf-8')
    css = (static_dir / 'styles.css').read_text(encoding='utf-8')
    icon_dir = static_dir / 'brand-icons'
    expected = {
        'dragon-spiral.svg','dragon-head.svg','spiked-dragon-head.svg','double-dragon.svg',
        'dragon-shield.svg','sea-dragon.svg','wyvern.svg','hydra.svg','dragon-orb.svg','fire-breath.svg'
    }
    assert expected.issubset({p.name for p in icon_dir.glob('*.svg')})
    assert (icon_dir / 'ATTRIBUTION.txt').exists()
    assert 'id="sidebarBrandLogo"' in index_html
    assert 'AGENT SYSTEM' not in index_html
    assert 'brand-icon-picker' in app_js
    assert 'backend_logo' in app_js
    assert '.brand-icon-option' in css

    with TestClient(app) as c:
        token = login(c, 'admin', 'ChangeMe123!')
        before = c.get('/api/system/branding', headers=auth(token))
        assert before.status_code == 200, before.text
        options = before.json()['available_icons']
        assert len(options) >= 10
        ids = {x['id'] for x in options}
        assert 'dragon-spiral' in ids and 'dragon-shield' in ids

        updated = c.patch('/api/system/branding', headers=auth(token), json={
            'backend_name': '天龙八部CPS后台',
            'player_center_name': '玩家中心',
            'backend_logo': 'dragon-shield',
        })
        assert updated.status_code == 200, updated.text
        assert updated.json()['backend_logo'] == 'dragon-shield'
        public = c.get('/api/public/system-branding')
        assert public.status_code == 200
        assert public.json()['backend_logo'] == 'dragon-shield'

        invalid = c.patch('/api/system/branding', headers=auth(token), json={
            'backend_name': '天龙八部CPS后台',
            'player_center_name': '玩家中心',
            'backend_logo': '../bad.svg',
        })
        assert invalid.status_code == 400


def test_v80_cps_accent_uses_fresh_assets_and_forced_cyan_style():
    """V80：CPS 必须拆分为独立元素，使用强制青蓝色，并更新静态资源版本。"""
    static_dir = Path(__file__).resolve().parents[1] / 'app' / 'static'
    index_html = (static_dir / 'index.html').read_text(encoding='utf-8')
    app_js = (static_dir / 'app.js').read_text(encoding='utf-8')
    css = (static_dir / 'styles.css').read_text(encoding='utf-8')

    assert 'V91 · ITEM IMPORT + GIFT LIMITS' in index_html
    assert '/static/styles.css?v=v91-item-import-gift-limits' in index_html
    assert '/static/app.js?v=v91-item-import-gift-limits' in index_html
    assert "accent.className='brand-name-segment brand-cps-accent'" in app_js
    assert 'renderSidebarBrandName(brand,backendName)' in app_js
    assert '.sidebar .brand .brand-name .brand-cps-accent' in css
    assert 'color:#39d7ff!important' in css
    assert '-webkit-text-fill-color:#39d7ff!important' in css
    assert 'font-size:inherit!important' in css


def test_v82_brand_title_segments_keep_inherited_size_and_long_name_compacts():
    """V82：切换页面/重新渲染后，品牌分段不能再次命中旧 26px span 样式；长名称自动缩小。"""
    static_dir = Path(__file__).resolve().parents[1] / 'app' / 'static'
    index_html = (static_dir / 'index.html').read_text(encoding='utf-8')
    app_js = (static_dir / 'app.js').read_text(encoding='utf-8')
    css = (static_dir / 'styles.css').read_text(encoding='utf-8')

    assert 'V91 · ITEM IMPORT + GIFT LIMITS' in index_html
    assert '/static/styles.css?v=v91-item-import-gift-limits' in index_html
    assert '/static/app.js?v=v91-item-import-gift-limits' in index_html
    assert "brand.classList.toggle('brand-name-long',visualLength>=9)" in app_js
    assert "brand.classList.toggle('brand-name-xlong',visualLength>=12)" in app_js
    assert '.brand-name .brand-name-segment' in css
    assert 'font-size:inherit!important' in css
    assert '.brand-name.brand-name-long{font-size:16px' in css
    assert '.brand-name.brand-name-xlong{font-size:14px' in css
    assert 'transform:none!important' in css


def test_v83_brand_legacy_span_rule_removed_and_login_brand_is_dynamic():
    """V83：旧 .brand span 不得再命中标题子片段；登录页必须读取同一后台品牌。"""
    from pathlib import Path
    static_dir = Path(__file__).resolve().parents[1] / "app" / "static"
    css = (static_dir / "styles.css").read_text(encoding="utf-8")
    js = (static_dir / "app.js").read_text(encoding="utf-8")
    index_html = (static_dir / "index.html").read_text(encoding="utf-8")

    assert ".brand span{font-size:26px" not in css
    assert ".brand > .brand-name{font-size:26px" in css
    assert 'id="loginBrandName"' in index_html
    assert 'id="loginBrandLogo"' in index_html
    assert "renderSidebarBrandName($('#loginBrandName'),backendName)" in js
    assert "await loadSystemBranding();" in js
    assert "V91 · ITEM IMPORT + GIFT LIMITS" in index_html
    assert "/static/styles.css?v=v91-item-import-gift-limits" in index_html
    assert "/static/app.js?v=v91-item-import-gift-limits" in index_html


def test_v87_player_center_has_no_brand_icon_and_keeps_dynamic_name():
    """V87：玩家中心不显示品牌图标，仅同步玩家中心名称；登录按钮恢复原深蓝色。"""
    static_dir = Path(__file__).resolve().parents[1] / 'app' / 'static'
    player_html = (static_dir / 'player_center.html').read_text(encoding='utf-8')
    assert 'id="playerLoginBrandLogo"' not in player_html
    assert 'id="playerTopbarBrandLogo"' not in player_html
    assert '--player-brand-logo-url' not in player_html
    assert "data?.backend_logo||'dragon-spiral'" not in player_html
    assert 'id="loginCenterName"' in player_html
    assert 'id="topbarCenterName"' in player_html
    assert '.brand strong{font-size:24px' in player_html
    assert '.topbar h2{font-size:20px' in player_html
    assert '.login-card .primary{width:100%;margin-top:22px;background:#1e3a70' in player_html
    assert 'linear-gradient(135deg,#2f80ed' not in player_html

    with TestClient(app) as c:
        token = login(c, 'admin', 'ChangeMe123!')
        updated = c.patch('/api/system/branding', headers=auth(token), json={
            'backend_name': '天龙八部CPS后台',
            'player_center_name': '天龙玩家中心',
            'backend_logo': 'dragon-orb',
        })
        assert updated.status_code == 200, updated.text
        public = c.get('/api/public/system-branding')
        assert public.status_code == 200
        assert public.json()['backend_logo'] == 'dragon-orb'
        assert public.json()['player_center_name'] == '天龙玩家中心'
        player = c.get('/player')
        assert player.status_code == 200
        assert player.headers.get('x-cps-build') == 'v91-item-import-gift-limits'
        assert 'no-store' in player.headers.get('cache-control','')
        assert 'id="playerLoginBrandLogo"' not in player.text
        assert 'id="playerTopbarBrandLogo"' not in player.text
        assert 'id="loginCenterName"' in player.text


def test_v89_agent_dashboard_hard_hides_commission_cards():
    """V89：普通代理数据总览前后端双重移除分佣字段与卡片。"""
    app_js = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.js").read_text(encoding="utf-8")
    dashboard_start = app_js.index("async function renderDashboard(){")
    dashboard_end = app_js.index("function registrationUrl(", dashboard_start)
    dashboard_block = app_js[dashboard_start:dashboard_end]
    assert "currentUser?.actor_type==='agent'||actorType==='agent'" in dashboard_block
    assert "dashboard_type==='superadmin'" not in dashboard_block
    assert "const commission=dashboardGroup('分佣数据'" not in dashboard_block
    assert "dashboardMetric('佣金比例'" not in dashboard_block
    assert "dashboardMetric('昨日分佣'" not in dashboard_block
    assert "dashboardMetric('今日分佣'" not in dashboard_block
    assert "dashboardMetric('总计分佣'" not in dashboard_block
    assert "${registration}${turnover}${dashboardRegistrationCard()}" in dashboard_block

    main_py = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(encoding="utf-8")
    dashboard_api_start = main_py.index('@app.get("/api/dashboard")')
    dashboard_api_end = main_py.index('@app.get("/api/system/metrics")', dashboard_api_start)
    dashboard_api = main_py[dashboard_api_start:dashboard_api_end]
    agent_return = dashboard_api[dashboard_api.index('"dashboard_type": "agent"'):]
    for key in ['commission_rate', 'yesterday_commission', 'today_commission', 'total_commission']:
        assert f'"{key}"' not in agent_return


def test_v90_game_item_library_and_reward_item_relations():
    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        h = auth(admin)
        item1 = c.post('/api/game-items', headers=h, json={'item_code':'ITEM-1001','name':'测试强化石','category':'强化材料','enabled':True})
        assert item1.status_code == 200, item1.text
        item2 = c.post('/api/game-items', headers=h, json={'item_code':'ITEM-1002','name':'测试宝石','category':'宝石','enabled':True})
        assert item2.status_code == 200, item2.text
        i1, i2 = item1.json()['id'], item2.json()['id']

        library = c.get('/api/game-items?enabled_only=true', headers=h)
        assert library.status_code == 200
        assert {x['item_code'] for x in library.json()} >= {'ITEM-1001','ITEM-1002'}

        gift = c.post('/api/products', headers=h, json={
            'sku':'V90-GIFT-001','name':'V90测试礼包','category':'gift','price':88,'stock':10,'description':'',
            'items':[{'item_id':i1,'quantity':3},{'item_id':i2,'quantity':9}],
        })
        assert gift.status_code == 200, gift.text
        gifts = c.get('/api/products?category=gift', headers=h)
        row = next(x for x in gifts.json() if x['sku']=='V90-GIFT-001')
        assert row['items'][0]['item_code']=='ITEM-1001'
        assert row['items'][0]['quantity']==3
        assert '测试强化石 × 3' in row['item_summary']
        assert '测试宝石 × 9' in row['item_summary']

        card = c.post('/api/privilege-cards', headers=h, json={
            'name':'V90测试周卡','card_type':'week','price_coins':66,'enabled':True,
            'items':[{'item_id':i1,'quantity':2}],
        })
        assert card.status_code == 200, card.text
        cards = c.get('/api/privilege-cards', headers=h)
        card_row = next(x for x in cards.json() if x['name']=='V90测试周卡')
        assert card_row['items'][0]['item_code']=='ITEM-1001'
        assert card_row['items'][0]['quantity']==2
        assert card_row['daily_reward_content']=='测试强化石 × 2'

        used_delete = c.delete(f'/api/game-items/{i1}', headers=h)
        assert used_delete.status_code == 409


def test_v90_item_picker_is_present_in_all_three_create_flows():
    static_dir = Path(__file__).resolve().parents[1] / 'app' / 'static'
    app_js = (static_dir / 'app.js').read_text(encoding='utf-8')
    index_html = (static_dir / 'index.html').read_text(encoding='utf-8')
    css = (static_dir / 'styles.css').read_text(encoding='utf-8')
    assert 'data-view="gameItems"' in index_html
    assert 'V91 · ITEM IMPORT + GIFT LIMITS' in index_html
    assert "['items',isGift?'礼包道具':'商品道具','item-builder'" in app_js
    assert "['items','每日奖励道具','item-builder'" in app_js
    assert 'function bindItemBuilders(root)' in app_js
    assert '.item-builder-picker' in css


def test_v91_game_item_library_imports_csv_json_and_xlsx():
    """V91：道具库可从多种文件格式批量导入，并按道具代码更新重复项。"""
    import io
    import json
    from openpyxl import Workbook

    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        h = auth(admin)

        csv_body = '道具ID,道具名称,分类,状态\nV91-CSV-1,CSV强化石,强化材料,启用\nV91-CSV-2,CSV宝石,宝石,1\n'.encode('utf-8')
        r = c.post('/api/game-items/import', headers=h, files={'file': ('items.csv', csv_body, 'text/csv')})
        assert r.status_code == 200, r.text
        assert r.json()['created'] == 2
        assert r.json()['skipped'] == 0

        json_body = json.dumps([
            {'item_code': 'V91-CSV-1', 'name': 'CSV强化石-更新', 'category': '高级材料', 'enabled': True},
            {'道具代码': 'V91-JSON-1', '道具名称': 'JSON宝箱', '分类': '宝箱', '状态': '启用'},
        ], ensure_ascii=False).encode('utf-8')
        r = c.post('/api/game-items/import', headers=h, files={'file': ('items.json', json_body, 'application/json')})
        assert r.status_code == 200, r.text
        assert r.json()['created'] == 1
        assert r.json()['updated'] == 1

        wb = Workbook()
        ws = wb.active
        ws.append(['item_code', 'name', 'category', 'enabled'])
        ws.append(['V91-XLSX-1', 'Excel神石', '神石', 1])
        buf = io.BytesIO(); wb.save(buf); wb.close()
        r = c.post('/api/game-items/import', headers=h, files={'file': ('items.xlsx', buf.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
        assert r.status_code == 200, r.text
        assert r.json()['created'] == 1

        rows = c.get('/api/game-items', headers=h).json()
        by_code = {x['item_code']: x for x in rows}
        assert by_code['V91-CSV-1']['name'] == 'CSV强化石-更新'
        assert by_code['V91-CSV-1']['category'] == '高级材料'
        assert by_code['V91-JSON-1']['name'] == 'JSON宝箱'
        assert by_code['V91-XLSX-1']['name'] == 'Excel神石'


def test_v91_gift_daily_weekly_monthly_and_lifetime_purchase_limits_are_enforced():
    """V91：礼包日/周/月/永久限购均由后端按玩家账号真实拦截。"""
    with TestClient(app) as c:
        admin = login(c, 'admin', 'ChangeMe123!')
        agent_resp = create_agent(c, admin, 'v91_limit_agent', 'V91限购代理', 1, 2)
        assert agent_resp.status_code == 200, agent_resp.text
        agent_id = agent_resp.json()['agent_id']
        reg = c.post(f'/api/public/registration/{agent_id}', json={
            'username': 'v91_limit_player', 'password': 'PlayerPass123!'
        })
        assert reg.status_code == 200, reg.text
        player_pk = reg.json()['id']
        issue = c.patch(f'/api/players/{player_pk}', headers=auth(admin), json={'coin_action':'issue','coin_amount':1000})
        assert issue.status_code == 200, issue.text
        player_token = c.post('/api/player/auth/login', json={'username':'v91_limit_player','password':'PlayerPass123!'}).json()['access_token']
        ph = auth(player_token)

        limit_cases = [
            ('DAILY', 'daily_limit', '每日'),
            ('WEEKLY', 'weekly_limit', '每周'),
            ('MONTHLY', 'monthly_limit', '每月'),
            ('LIFETIME', 'lifetime_limit', '永久'),
        ]
        gift_ids = []
        for suffix, field, label in limit_cases:
            payload = {
                'sku': f'V91-{suffix}', 'name': f'V91{label}限购礼包', 'category':'gift',
                'price': 10, 'stock': 5, 'description':'', field: 1,
            }
            created = c.post('/api/products', headers=auth(admin), json=payload)
            assert created.status_code == 200, created.text
            gift_ids.append((created.json()['id'], field, label))

        admin_rows = c.get('/api/products?category=gift', headers=auth(admin)).json()
        for gift_id, field, label in gift_ids:
            row = next(x for x in admin_rows if x['id'] == gift_id)
            assert row[field] == 1
            assert f'{label}限购 1 次' in row['purchase_limit_text']

            before = next(x for x in c.get('/api/player/mall/products', headers=ph).json() if x['id'] == gift_id)
            assert before['available'] is True
            period_key = {'daily_limit':'daily','weekly_limit':'weekly','monthly_limit':'monthly','lifetime_limit':'lifetime'}[field]
            assert before['purchase_limit_status']['periods'][period_key]['remaining'] == 1

            first = c.post(f'/api/player/mall/purchase/{gift_id}', headers=ph, json={'quantity':1})
            assert first.status_code == 200, first.text
            second = c.post(f'/api/player/mall/purchase/{gift_id}', headers=ph, json={'quantity':1})
            assert second.status_code == 400, second.text
            assert label in second.json()['detail'] and '限购' in second.json()['detail']

            after = next(x for x in c.get('/api/player/mall/products', headers=ph).json() if x['id'] == gift_id)
            assert after['available'] is False
            assert label in after['unavailable_reason']
            assert after['purchase_limit_status']['periods'][period_key]['remaining'] == 0
