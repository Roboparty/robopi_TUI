"""CAN discovery must not hide an unconfigured or unhealthy HPM interface."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from loader import board_backend as backend


class CanConfigurationTests(unittest.TestCase):
    def raw(self):
        usb = dict(vid='1209', pid='2323', path='/sys/devices/hpm')
        links = [dict(ifname=f'can{i}', driver='gs_usb', usbParent=usb,
                      flags=['NOARP', 'UP', 'LOWER_UP', 'ECHO'], mtu=72,
                      operstate='UP', txqlen=10000,
                      linkinfo=dict(info_kind='can', info_data=dict(
                          ctrlmode=['FD'], state='ERROR-ACTIVE',
                          bittiming=dict(bitrate=1000000),
                          data_bittiming=dict(bitrate=5000000)))) for i in range(4)]
        raw = dict(identity=dict(hostname='board', uid=0),
                   endIdentity=dict(hostname='board', uid=0), collectedAt='now')
        for name in ('system', 'resources', 'network', 'udev', 'firmware', 'sources', 'services'):
            raw[name] = dict(data=[], error=None)
        raw.update(usb=dict(data=[usb], error=None), can=dict(data=links, error=None),
                   packages=dict(data=dict(items=[]), error=None))
        return raw

    def snapshot(self, raw):
        with patch.object(backend.board_snapshot, 'collect', return_value=raw):
            return backend.snapshot()

    def test_down_interfaces_are_reported_separately_from_successful_discovery(self):
        raw = self.raw()
        for link in raw['can']['data']:
            link.update(flags=['NOARP', 'ECHO'], operstate='DOWN', mtu=16, txqlen=10)
            link['linkinfo']['info_data'] = dict(state='STOPPED')
        data = self.snapshot(raw)
        self.assertEqual('pass', data['can']['outcome'])
        problem_rows = [p for p in data['parameters'] if p['group'] == 'CAN' and p['outcome'] == 'fail']
        self.assertTrue(problem_rows, 'DOWN HPM interfaces are currently all displayed as pass')
        self.assertIn('DOWN', ' '.join(p['value'] for p in problem_rows))
        self.assertEqual('fail', data['canConfiguration']['outcome'])
        for action in ('can-check', 'hpm-flash', 'pipeline'):
            with self.subTest(action=action), patch.object(backend, 'snapshot', return_value=data), \
                    patch.object(backend, 'preflight'), patch.object(backend, 'upgrade'), \
                    patch.object(backend, 'install_and_flash', return_value={'refreshedAt': 'now'}):
                result = backend.execute_action(action, lambda *_: None)
                self.assertEqual('needs_review', result['outcome'])
                self.assertIn('CAN', result['summary'])

    def test_healthy_configuration_does_not_claim_frame_communication(self):
        data = self.snapshot(self.raw())
        self.assertEqual('pass', data['canConfiguration']['outcome'])
        self.assertIn('未发送', data['canConfiguration']['message'])

    def test_configuration_checks_each_hpm_channel_and_preserves_raw_evidence(self):
        for field, value, expected in [('mtu', 16, 'MTU'), ('txqlen', 10, '队列')]:
            with self.subTest(field=field):
                raw = self.raw()
                raw['can']['data'][2][field] = value
                data = self.snapshot(raw)
                self.assertEqual('fail', data['canConfiguration']['outcome'])
                self.assertIn('can2', data['canConfiguration']['message'])
                self.assertIn(expected, data['canConfiguration']['message'])
                self.assertEqual(value, data['raw']['can']['data'][2][field])
        for field, value, expected in [('ctrlmode', [], 'FD'), ('state', 'BUS-OFF', 'BUS-OFF'),
                                       ('bittiming', {'bitrate': 500000}, '1000000'),
                                       ('data_bittiming', {'bitrate': 2000000}, '5000000')]:
            with self.subTest(field=field):
                raw = self.raw()
                raw['can']['data'][1]['linkinfo']['info_data'][field] = value
                data = self.snapshot(raw)
                self.assertEqual('fail', data['canConfiguration']['outcome'])
                self.assertIn(expected, data['canConfiguration']['message'])

    def test_missing_evidence_is_not_a_pass_and_other_can_ports_are_not_subject_to_hpm_rules(self):
        raw = self.raw()
        other = copy.deepcopy(raw['can']['data'][0])
        other.update(ifname='can_top', driver='rockchip_canfd', usbParent=None, flags=[], mtu=16)
        raw['can']['data'].append(other)
        self.assertEqual('pass', self.snapshot(raw)['canConfiguration']['outcome'])
        raw['can']['data'][0].pop('linkinfo')
        self.assertNotEqual('pass', self.snapshot(raw)['canConfiguration']['outcome'])
        raw['can']['data'].pop(1)
        data = self.snapshot(raw)
        self.assertEqual('fail', data['can']['outcome'])
        self.assertNotEqual('pass', data['canConfiguration']['outcome'])


if __name__ == '__main__':
    unittest.main()
