#!/usr/bin/env python3
"""Read-only STANDARD LIBRARY integrity check. Does not revalidate molecular science."""
import argparse
import json
import math
from pathlib import Path
import sys

from common import APPROVED_PROFILE_SHA,INPUT_NAMES,REQUIRED_OUTPUTS,box_parameters,read_json,safe,sha


def check(root):
    issues=[];checked=0
    def need(condition,code,**details):
        if not condition:issues.append({'code':code,**details})
    state='FAIL'
    try:
        manifest=read_json(root/'records/manifest.json');listed={}
        for row in manifest['files']:
            name=row['path'];path=safe(root,name);need(name not in listed,'DUPLICATE_PATH',path=name);listed[name]=row['sha256']
            need(path.is_file(),'MISSING_FILE',path=name)
            if path.is_file():
                need(path.stat().st_size==row['bytes'] and sha(path)==row['sha256'],'HASH_OR_SIZE_MISMATCH',path=name);checked+=1
        sums={}
        for line in (root/'SHA256SUMS').read_text().splitlines():
            digest,name=line.split('  ',1);safe(root,name);need(name not in sums,'DUPLICATE_CHECKSUM_PATH',path=name);sums[name]=digest
        need(sums==listed,'MANIFEST_CHECKSUM_DISAGREE')
        need({'job.json','run_status.json','run_metadata.json'}<=set(listed),'MISSING_CONTRACT_FILES')
        job=read_json(root/'job.json');status=read_json(root/'run_status.json');state=job['preparation_status']
        need(manifest['job_id']==job['job_id']==status['job_id'],'JOB_ASSOCIATION_MISMATCH')
        need(status['status']==state and job['ready_for_03']==(state=='READY_FOR_03'),'STATUS_MISMATCH')
        if state=='READY_FOR_03':
            need({'profile.json','request.json'}<=set(listed),'MISSING_READY_INPUT_CONTRACT')
            need(status['exit_code']==0 and not job['blockers'],'READY_WITH_FAILURE')
            need(job['mode']=='smoke_test' and job['stage03_status']=='待联调' and not job['vina_read_performed'],'UNSUPPORTED_STATE_CLAIM')
            profile=read_json(root/'profile.json');need(sha(root/'profile.json')==job['prepare_profile']['sha256']==APPROVED_PROFILE_SHA,'PROFILE_HASH_MISMATCH')
            need(profile['profile_id']==job['prepare_profile']['id'] and profile['version']==job['prepare_profile']['version'],'PROFILE_ASSOCIATION_MISMATCH')
            request=read_json(root/'request.json');metadata=read_json(root/'run_metadata.json')
            need(job['request']['path']=='request.json' and sha(root/'request.json')==job['request']['sha256'],'REQUEST_HASH_MISMATCH')
            need(request['job_id']==metadata['job_id']==job['job_id'] and request['mode']==job['mode'] and metadata['status']==state and metadata['exit_code']==0,'REQUEST_RUNTIME_ASSOCIATION_MISMATCH')
            need(request['prepare_profile']['sha256']==job['prepare_profile']['sha256'] and request['target_id']==job['target_id'] and request['model']==job['model'],'REQUEST_PROFILE_OR_TARGET_MISMATCH')
            need(request['protein']=={'auth_asym_id':job['protein']['auth_asym_id'],'label_asym_id':job['protein']['label_asym_id']} and all(request['reference_ligand'][k]==job['reference_ligand'][k] for k in request['reference_ligand']),'REQUEST_SELECTION_ASSOCIATION_MISMATCH')
            need(request['search_box']==profile['search_box'] and all(job['search_box'][k]==request['search_box'][k] for k in ['method','padding','unit']),'REQUEST_BOX_POLICY_MISMATCH')
            for role,relative in INPUT_NAMES.items():
                row=job['inputs'][role];path=safe(root,row['path'])
                need(row['path']==relative and row['path'] in listed and path.is_file() and sha(path)==row['sha256'],'INPUT_HASH_OR_PATH_MISMATCH',role=role)
            for role,field in [('structure','source_structure'),('sequence','sequence'),('handoff','handoff'),('RXT_CCD','chemical_definition')]:need(request[field]['sha256']==job['inputs'][role]['sha256']==profile['expected_source_sha256'][role],'REQUEST_INPUT_ASSOCIATION_MISMATCH',role=role)
            need(job['source_structure']['path']==job['inputs']['structure']['path'] and job['source_structure']['sha256']==job['inputs']['structure']['sha256'],'SOURCE_ASSOCIATION_MISMATCH')
            for role,relative in REQUIRED_OUTPUTS.items():
                row=job['outputs'][role];path=safe(root,row['path'])
                need(row['path']==relative and row['state']=='AVAILABLE' and row['path'] in listed and path.is_file() and sha(path)==row['sha256'] and path.stat().st_size==row['bytes'],'OUTPUT_HASH_OR_PATH_MISMATCH',role=role)
            binding=read_json(root/'records/box_binding.json');need('records/box_binding.json' in listed,'UNLISTED_BINDING')
            for path_key,sha_key,role in [('prepared_receptor_pdbqt','prepared_receptor_pdbqt_sha256','receptor'),('ligand_pdbqt','ligand_pdbqt_sha256','ligand'),('reference_sdf','reference_sdf_sha256','reference'),('selected_protein_pdb','selected_protein_sha256','selected_protein')]:
                need(binding[path_key]==job['outputs'][role]['path'] and binding[sha_key]==job['outputs'][role]['sha256'],'BOX_BINDING_MISMATCH',role=role)
            need(binding['source_structure']==job['inputs']['structure']['path'] and binding['source_structure_sha256']==job['inputs']['structure']['sha256']==job['search_box']['coordinate_source_sha256'],'BOX_SOURCE_MISMATCH')
            need(binding['padding_angstrom']==job['search_box']['padding'],'BOX_PADDING_MISMATCH')
            need(binding['prepared_receptor_pdbqt_sha256']==job['search_box']['prepared_receptor_sha256'],'BOX_RECEPTOR_MISMATCH')
            parameters=box_parameters(safe(root,job['outputs']['vina_box']['path']))
            expected=dict(zip(['center_x','center_y','center_z','size_x','size_y','size_z'],job['search_box']['center']+job['search_box']['size']))
            need(set(binding['parameters'])==set(expected) and all(math.isclose(parameters[k],v,abs_tol=1e-6,rel_tol=0) and math.isclose(binding['parameters'][k],v,abs_tol=1e-6,rel_tol=0) for k,v in expected.items()),'BOX_VALUES_MISMATCH')
            need(job['search_box']['unit']==binding['unit']=='angstrom','BOX_UNIT_MISMATCH')
        else:need(state in ('BLOCKED','FAIL') and job['ready_for_03'] is False,'NONFINAL_OR_FALSE_READY_STATE')
    except Exception as error:issues.append({'code':'INTEGRITY_CHECK_ERROR','error':type(error).__name__+': '+str(error)})
    code=1 if issues or state=='FAIL' else 2 if state=='BLOCKED' else 0
    return {'status':'FAIL' if code==1 else 'BLOCKED' if code==2 else 'INTEGRITY_PASS','exit_code':code,'read_only':True,'checked_files':checked,
            'declared_preparation_status':state,'scope':'paths, hashes, status and bindings only','scientific_content_revalidated':False,'issues':issues}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,required=True);args=parser.parse_args()
    result=check(args.root.resolve());print(json.dumps(result,ensure_ascii=False,indent=2));sys.exit(result['exit_code'])
