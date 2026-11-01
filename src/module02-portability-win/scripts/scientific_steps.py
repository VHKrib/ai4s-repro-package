#!/usr/bin/env python3
"""Invoke the extracted approved recipe in a fresh run directory; outputs go to step logs."""
import argparse
import importlib.metadata as md
import json
from pathlib import Path
import platform
import sys

import meeko

from common import read_json,sha,write_json
from recipes import audit,derive,prepare_ligand_sdf,validate_ligand,make_box
from receptor_content import verify_receptor


def environment(root):
    profile=read_json(root/'profile.json');actual={name:md.version(name) for name in profile['locked_environment'] if name!='python'}
    actual['python']=platform.python_version()
    if actual!=profile['locked_environment']:
        print(json.dumps({'status':'BLOCKED','code':'UNSUPPORTED_ENVIRONMENT','expected':profile['locked_environment'],'actual':actual}));return 2
    template=Path(meeko.__file__).parent/'data/residue_chem_templates.json'
    if sha(template)!=profile['meeko_template_file_sha256']:
        print(json.dumps({'status':'BLOCKED','code':'TEMPLATE_HASH_NOT_APPROVED'}));return 2
    write_json(root/'records/runtime_environment.json',{'executable':sys.executable,'architecture':platform.machine(),'versions':actual,'meeko_template_sha256':sha(template),'dependencies_added':False})
    print(json.dumps({'status':'PASS','versions':actual,'architecture':platform.machine(),'meeko_template_sha256':sha(template)}));return 0


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['environment','audit','derive','ligand-sdf','validate-receptor','validate-ligand','box'])
    parser.add_argument('--root',required=True,type=Path);args=parser.parse_args();root=args.root.resolve()
    if args.action=='environment':sys.exit(environment(root))
    elif args.action=='audit':audit(root)
    elif args.action=='derive':derive(root)
    elif args.action=='ligand-sdf':prepare_ligand_sdf(root)
    elif args.action=='validate-ligand':validate_ligand(root)
    elif args.action=='box':make_box(root,read_json(root/'profile.json')['search_box']['padding'])
    elif args.action=='validate-receptor':
        result=verify_receptor(root);write_json(root/'records/receptor_validation.json',result)
        print(json.dumps({k:v for k,v in result.items() if k not in ['heavy_atom_mapping','modeled_hydrogens','all_residue_templates','atom_name_changes']},indent=2))
