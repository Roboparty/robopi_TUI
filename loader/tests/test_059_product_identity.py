# SPDX-License-Identifier: GPL-3.0
from pathlib import Path
import json
import runpy
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = runpy.run_path(str(ROOT / 'plugins' / '059-product_identity.py'))


class ProductIdentityTests(unittest.TestCase):
    def _paths(self, root):
        robots = root / 'robots'
        (robots / 'rpo').mkdir(parents=True)
        (robots / 'rpo' / 'robot.yaml').write_text('robot:\n  type: rpo\n')
        return root / 'product.json', root / 'factory', robots

    def test_provisions_product_and_factory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            product_file, factory, robots = self._paths(root)
            result = PLUGIN['probe']('rpo', sku='RPO-1', force='no', board='rdkx5',
                                     product_file=product_file, factory_dir=factory,
                                     robots_dir=robots, require_root=False)
            self.assertEqual(result['status'], 'pass')
            selected = json.loads(product_file.read_text())
            self.assertEqual(selected['product'], 'rpo')
            self.assertEqual(selected['dof'], 23)
            identity = json.loads((factory / 'product.json').read_text())
            self.assertTrue(identity['provisioned'])
            self.assertEqual(identity['generation'], 1)
            self.assertEqual(identity['board'], 'rdkx5')

    def test_rejects_board_product_mismatch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            product_file, factory, robots = self._paths(root)
            result = PLUGIN['probe']('rp1', board='rdkx5', product_file=product_file,
                                     factory_dir=factory, robots_dir=robots, require_root=False)
            self.assertEqual(result['status'], 'needs_review')

    def test_requires_force_to_replace_existing_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            product_file, factory, robots = self._paths(root)
            args = dict(product_file=product_file, factory_dir=factory, robots_dir=robots,
                        require_root=False, board='rdkx5')
            self.assertEqual(PLUGIN['probe']('rpo', **args)['status'], 'pass')
            self.assertEqual(PLUGIN['probe']('rpo', **args)['status'], 'needs_review')
            result = PLUGIN['probe']('rpo', force='yes', **args)
            self.assertEqual(result['status'], 'pass')
            self.assertEqual(result['details']['generation'], 2)

    def test_x86_simulation_has_no_board_profile_requirement(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            product_file, factory, robots = self._paths(root)
            result = PLUGIN['probe']('rpo-sim', board='x86_64', product_file=product_file,
                                     factory_dir=factory, robots_dir=robots, require_root=False)
            self.assertEqual(result['status'], 'pass')


if __name__ == '__main__':
    unittest.main()
