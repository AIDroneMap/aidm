import unittest
import os
from modules.osfm import get_submodel_argv, get_submodel_args_dict
from modules import config

class TestOSFM(unittest.TestCase):
    def setUp(self):
        pass

    def test_get_submodel_argv(self):
        # Base
        args = config.config(["--project-path", "/datasets"])
        
        self.assertEqual(get_submodel_argv(args)[1:], 
            ['--orthophoto-cutline', '--dem-euclidean-map', '--skip-3dmodel', '--skip-report'])
        self.assertEqual(get_submodel_argv(args, "/submodels", "submodel_0000")[1:], 
            ['--orthophoto-cutline', '--dem-euclidean-map', '--skip-3dmodel', '--skip-report', '--project-path', '/submodels', 'submodel_0000'])        

        # Base + project name
        args = config.config(["--project-path", "/datasets", "brighton"])
        self.assertEqual(get_submodel_argv(args)[1:], 
            ['--orthophoto-cutline', '--dem-euclidean-map', '--skip-3dmodel', '--skip-report'])
        self.assertEqual(get_submodel_argv(args, "/submodels", "submodel_0000")[1:], 
            ['--orthophoto-cutline', '--dem-euclidean-map', '--skip-3dmodel', '--skip-report', '--project-path', '/submodels', 'submodel_0000'])        

        # Project name + base
        args = config.config(["brighton", "--project-path", "/datasets"])
        self.assertEqual(get_submodel_argv(args)[1:], 
            ['--orthophoto-cutline', '--dem-euclidean-map', '--skip-3dmodel', '--skip-report'])
        self.assertEqual(get_submodel_argv(args, "/submodels", "submodel_0000")[1:], 
            ['--orthophoto-cutline', '--dem-euclidean-map', '--skip-3dmodel', '--skip-report', '--project-path', '/submodels', 'submodel_0000'])        

        # Crop
        args = config.config(["brighton", "--project-path", "/datasets", "--crop", "0"])
        self.assertEqual(get_submodel_argv(args)[1:], 
            ['--crop', '0.015625', '--orthophoto-cutline', '--dem-euclidean-map', '--skip-3dmodel', '--skip-report'])
        self.assertEqual(get_submodel_argv(args, "/submodels", "submodel_0000")[1:], 
            ['--crop', '0.015625', '--orthophoto-cutline', '--dem-euclidean-map', '--skip-3dmodel', '--skip-report', '--project-path', '/submodels', 'submodel_0000'])        

        # With sm-cluster, pc-csv and others
        args = config.config(["--project-path", "/datasets", "--split", "200", "--pc-csv"])
        self.assertEqual(get_submodel_argv(args)[1:], 
            ['--orthophoto-cutline', '--dem-euclidean-map', '--skip-3dmodel', '--skip-report'])
        self.assertEqual(get_submodel_argv(args, "/submodels", "submodel_0000")[1:], 
            ['--orthophoto-cutline', '--dem-euclidean-map', '--skip-3dmodel', '--skip-report', '--project-path', '/submodels', 'submodel_0000'])        

        # Cameras JSON
        args = config.config(["--project-path", "/datasets", "--cameras", os.path.join(os.path.dirname(os.path.realpath(__file__)), "assets", "sample.json")])
        self.assertEqual(get_submodel_argv(args)[1:], 
            ['--cameras', '{"test": "1"}', '--orthophoto-cutline', '--dem-euclidean-map', '--skip-3dmodel', '--skip-report'])
        
        # Camera JSON string
        args = config.config(["--project-path", "/datasets", "--cameras", '{"test": "1"}'])
        self.assertEqual(get_submodel_argv(args)[1:], 
            ['--cameras', '{"test": "1"}', '--orthophoto-cutline', '--dem-euclidean-map', '--skip-3dmodel', '--skip-report'])
    
    def test_get_submodel_argv_dict(self):
        # Base
        args = config.config(["--project-path", "/datasets"])
        
        self.assertEqual(get_submodel_args_dict(args), 
            {'orthophoto-cutline': True, 'skip-3dmodel': True, 'dem-euclidean-map': True, 'skip-report': True})

    def test_get_submodel_argv_drops_copy_to(self):
        args = config.config(["--project-path", "/datasets", "--copy-to", "/target", "--crop", "5", "--cog"])
        expected = ['--crop', '5.0', '--orthophoto-cutline', '--dem-euclidean-map', '--skip-3dmodel', '--skip-report']

        # No copy target reaches a submodel, and the other options are unchanged
        # (--cog is dropped as before)
        for argv in (get_submodel_argv(args),
                     get_submodel_argv(args, "/submodels", "submodel_0000")):
            self.assertNotIn('--copy-to', argv)
            self.assertNotIn('/target', argv)

        self.assertEqual(get_submodel_argv(args)[1:], expected)
        self.assertEqual(get_submodel_argv(args, "/submodels", "submodel_0000")[1:],
            expected + ['--project-path', '/submodels', 'submodel_0000'])

        self.assertNotIn('copy-to', get_submodel_args_dict(args))

if __name__ == '__main__':
    unittest.main()