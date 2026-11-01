#!/usr/bin/env python3
"""Task-focused integration tests: raw rebuild, relocation, bad inputs, integrity and no overwrite."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def snapshot(root):return {str(p.relative_to(root)):sha(p) for p in root.rglob('*') if p.is_file()}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--evidence-dir',default='records/module_tests');parser.add_argument('--run-out',default='runs/jak2_module_001');args=parser.parse_args()
    evidence=ROOT/args.evidence_dir;evidence.mkdir(exist_ok=False)
    temporary=Path(tempfile.mkdtemp(prefix='A4S MODULE move '));cases=[]
    def invoke(name,command,cwd,expected,readonly_root=None):
        before=snapshot(readonly_root) if readonly_root else None
        result=subprocess.run(command,cwd=cwd,capture_output=True,text=True)
        (evidence/(name+'.stdout.json')).write_text(result.stdout);(evidence/(name+'.stderr.log')).write_text(result.stderr)
        record={'case':name,'command':command,'cwd':str(cwd),'exit_code':result.returncode,'expected_exit':expected}
        if readonly_root:record['all_file_hashes_unchanged']=before==snapshot(readonly_root)
        with (evidence/'commands.jsonl').open('a') as h:h.write(json.dumps(record)+'\n')
        assert result.returncode==expected,(name,result.stdout,result.stderr)
        if readonly_root:assert record['all_file_hashes_unchanged'],name
        data=json.loads(result.stdout);record['status']=data['status'];cases.append(record);return data
    def prepare(name,module,request,out,expected,flags=()):
        data=invoke(name,[sys.executable,*flags,'-B',str(module/'scripts/run_prepare.py'),'--request',str(request),'--out',str(out)],module,expected)
        if expected:
            assert data['status']!='READY_FOR_03'
            if out.is_dir() and name!='E_existing_output':
                job=json.loads((out/'job.json').read_text());assert job['ready_for_03'] is False and job['preparation_status'] in ('BLOCKED','FAIL')
                parsed_request=json.loads(request.read_text())
                assert job['job_id']==parsed_request['job_id'] and job['request']['sha256']==sha(request),'Failure must remain associated with its actual request'
        return data
    try:
        normal=ROOT/args.run_out
        assert not normal.exists(),'A must use a new output directory'
        data=prepare('A_raw_to_complete',ROOT,ROOT/'cases/jak2_6vgl.json',normal,0)
        assert data['status']=='READY_FOR_03' and data['integrity_status']=='INTEGRITY_PASS'
        staging=json.loads((normal/'records/staging.json').read_text());assert staging['prepared_products_present'] is False
        assert all(not r['path'].endswith(('.sdf','.pdbqt','.pdb','.txt')) for r in staging['files'])
        commands=[json.loads(line) for line in (normal/'records/commands.jsonl').read_text().splitlines()]
        assert all(r['exit_code']==0 for r in commands)
        assert any('meeko.cli.mk_prepare_receptor' in r['command'] for r in commands) and any('meeko.cli.mk_prepare_ligand' in r['command'] for r in commands)
        baseline=json.loads((ROOT/'records/baseline_r1_hashes.json').read_text())
        r1=[{'path':p,'R1_sha256':h,'module_sha256':sha(normal/p),'byte_identical':h==sha(normal/p)} for p,h in baseline['files'].items()]
        assert all(r['byte_identical'] for r in r1),'Science file differs from accepted R1; report for review'
        invoke('A_stdlib_receiver',[sys.executable,'-S','-B',str(ROOT/'scripts/check_integrity.py'),'--root',str(normal)],ROOT,0,normal)
        invoke('A_full_science_receiver',[sys.executable,'-B',str(ROOT/'scripts/check_science.py'),'--root',str(normal)],ROOT,0,normal)
        moved=temporary/'package with spaces';moved.mkdir()
        for name in ['scripts','inputs','profiles','definitions','cases']:shutil.copytree(ROOT/name,moved/name)
        shutil.copy2(ROOT/'requirements.lock.txt',moved/'requirements.lock.txt')
        assert not any((moved/p).exists() for p in ['runs','outputs','EvidenceScope','module02-smoke-r1'])
        request=json.loads((moved/'cases/jak2_6vgl.json').read_text());request['job_id']='A4S-02-MODULE-001-MOVED-002';(moved/'cases/jak2_6vgl.json').write_text(json.dumps(request,indent=2)+'\n')
        migrated=moved/'results with spaces/run 002';prepare('B_path_migration',moved,moved/'cases/jak2_6vgl.json',migrated,0)
        compared=[{'path':p,'normal_sha256':sha(normal/p),'moved_sha256':sha(migrated/p),'byte_identical':sha(normal/p)==sha(migrated/p)} for p in baseline['files']]
        assert all(r['byte_identical'] for r in compared)
        def science_summary(root):
            receptor=json.loads((root/'records/receptor_validation.json').read_text());ligand=json.loads((root/'records/ligand_validation.json').read_text());job=json.loads((root/'job.json').read_text())
            return {'receptor':{k:receptor[k] for k in ['original_heavy_atoms_retained','max_experimental_heavy_coordinate_change_angstrom','PTR_formal_charge','N_terminal_N_formal_charge','C_terminal_carboxylate_formal_charge','PDBQT_total_atoms']},
                    'ligand':{k:ligand[k] for k in ['canonical_isomeric_smiles','formal_charge','stereocenters','max_heavy_coordinate_displacement_angstrom']},'box':job['search_box'],'profile_sha256':job['prepare_profile']['sha256']}
        assert science_summary(normal)==science_summary(migrated)
        a=json.loads((normal/'run_metadata.json').read_text());b=json.loads((migrated/'run_metadata.json').read_text())
        assert a['job_id']!=b['job_id'] and a['output_directory']!=b['output_directory'] and a['started_utc']!=b['started_utc']
        assert json.loads((migrated/'job.json').read_text())['job_id']==request['job_id']==b['job_id']
        invoke('B_stdlib_receiver',[sys.executable,'-S','-B',str(moved/'scripts/check_integrity.py'),'--root',str(migrated)],moved,0,migrated)
        migration_evidence=evidence/'migration_evidence';migration_evidence.mkdir()
        for relative,name in [('job.json','job_snapshot.json'),('run_metadata.json','runtime_snapshot.json'),('records/commands.jsonl','actual_commands.jsonl'),('records/staging.json','staging.json'),('records/full_science_check.json','full_science_check.json'),('records/logs/05-meeko-receptor.stdout.log','meeko_receptor.stdout.log'),('records/logs/07-meeko-ligand.stdout.log','meeko_ligand.stdout.log')]:shutil.copy2(migrated/relative,migration_evidence/name)
        comparison={'R1_baseline_byte_comparisons':r1,'migration_core_byte_comparisons':compared,'scientific_identity_coordinates_parameters_equal':True,
                    'metadata_byte_equality_required':False,'expected_metadata_differences':['job_id','start/end time','output directory','command paths'],'metadata_associations_correct':True}
        (evidence/'scientific_vs_metadata_comparison.json').write_text(json.dumps(comparison,indent=2)+'\n')
        for case,mutate,expected in [('C_wrong_chain',lambda r:r['protein'].update({'label_asym_id':'X'}),2),
                                    ('C_wrong_source_hash',lambda r:r['source_structure'].update({'sha256':'0'*64}),1),
                                    ('C_missing_sequence',lambda r:r.pop('sequence'),2)]:
            req=json.loads((moved/'cases/jak2_6vgl.json').read_text());mutate(req);path=moved/'cases'/(case+'.json');path.write_text(json.dumps(req,indent=2)+'\n')
            out=moved/'negative'/case;prepare(case,moved,path,out,expected)
            assert not (out/'outputs/receptor.pdbqt').exists() and not (out/'outputs/ligand.pdbqt').exists()
            shutil.copy2(out/'run_status.json',evidence/(case+'.run_status.json'))
        profile=json.loads((moved/'profiles/jak2_6vgl_rxt_v1.json').read_text());profile['exclude_only_CIF_expression_tag_auth_range']=[825,850]
        altered=moved/'profiles/unapproved.json';altered.write_text(json.dumps(profile,indent=2)+'\n')
        req=json.loads((moved/'cases/jak2_6vgl.json').read_text());req['prepare_profile'].update({'path':'../profiles/unapproved.json','sha256':sha(altered)})
        path=moved/'cases/unapproved.json';path.write_text(json.dumps(req,indent=2)+'\n');prepare('C_unapproved_profile',moved,path,moved/'negative/profile',2)
        prepare('missing_dependencies_explicit',moved,moved/'cases/jak2_6vgl.json',moved/'negative/no_site',1,flags=('-S',))
        tampered=temporary/'tampered result';shutil.copytree(normal,tampered);path=tampered/'outputs/ligand.pdbqt';path.write_bytes(path.read_bytes()+b'REMARK deliberate integrity test\n')
        data=invoke('D_tampered_output',[sys.executable,'-S','-B',str(ROOT/'scripts/check_integrity.py'),'--root',str(tampered)],ROOT,1,tampered)
        assert any(r['code']=='HASH_OR_SIZE_MISMATCH' for r in data['issues'])
        before=snapshot(normal);prepare('E_existing_output',ROOT,ROOT/'cases/jak2_6vgl.json',normal,1);assert before==snapshot(normal)
        summary={'status':'PASS','cases':cases,'normal_run':str(normal),'migration_run':str(migrated),'temporary_root':str(temporary),
                 'raw_data_rebuild_demonstrated':True,'core_R1_files_byte_identical':len(r1),'core_migration_files_byte_identical':len(compared),
                 'scientific_and_runtime_metadata_evaluated_separately':True,'existing_output_unchanged':True,'third_party_science_imports_not_needed_for_integrity':True}
    except Exception as error:summary={'status':'FAIL','cases':cases,'temporary_root':str(temporary),'error':type(error).__name__+': '+str(error)}
    (evidence/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n');print(json.dumps(summary,ensure_ascii=False,indent=2));return 0 if summary['status']=='PASS' else 1


if __name__=='__main__':sys.exit(main())
