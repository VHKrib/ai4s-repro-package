#!/usr/bin/env python3
"""One request -> newly generated approved JAK2 inputs. Final stdout is one JSON object."""
import argparse
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from common import APPROVED_PROFILE_SHA,INPUT_NAMES,REQUIRED_OUTPUTS,PreparationError,file_record,read_json,sha,write_json,write_manifest
from check_integrity import check as integrity_check

SCRIPTS=Path(__file__).resolve().parent
MODULE_VERSION='1.0.0'


def now():return dt.datetime.now(dt.timezone.utc).isoformat()


def input_file(base,spec,role):
    if not isinstance(spec,dict) or not {'path','sha256'}<=set(spec):raise PreparationError('MISSING_INPUT_SPEC','Missing path/hash for '+role,2)
    if not isinstance(spec['path'],str) or not spec['path'] or Path(spec['path']).is_absolute():raise PreparationError('INPUT_PATH_MUST_BE_RELATIVE',role,2)
    if not isinstance(spec['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',spec['sha256']):raise PreparationError('INVALID_INPUT_HASH','Need full SHA-256 for '+role,2)
    path=(base/spec['path']).resolve()
    if not path.is_file():raise PreparationError('MISSING_INPUT_FILE',role,2,path=spec['path'])
    actual=sha(path)
    if actual!=spec['sha256']:raise PreparationError('INPUT_HASH_MISMATCH',role,1,expected=spec['sha256'],actual=actual)
    return path


def validate_request(path):
    if not path.is_file():raise PreparationError('MISSING_REQUEST',str(path),2)
    try:request=read_json(path)
    except (ValueError,UnicodeError) as error:raise PreparationError('INVALID_REQUEST_JSON',str(error),2)
    keys={'schema_version','job_id','mode','target_id','source_structure','sequence','handoff','chemical_definition','prepare_profile','model','protein','reference_ligand','search_box'}
    if not isinstance(request,dict) or set(request)!=keys:raise PreparationError('REQUEST_FIELDS_NOT_SUPPORTED','Required keys must be complete; unknown policy strings are rejected',2)
    if any(not isinstance(request[key],dict) for key in ['source_structure','sequence','handoff','chemical_definition','prepare_profile','protein','reference_ligand','search_box']):raise PreparationError('INVALID_REQUEST_OBJECT','Nested input/selection fields must be JSON objects',2)
    if request['schema_version']!='a4s.prepare_request.v1' or not isinstance(request['job_id'],str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9._-]{0,95}',request['job_id']):raise PreparationError('REQUEST_SCHEMA_OR_ID_NOT_SUPPORTED','Unsupported schema/job_id',2)
    base=path.parent;profile_path=input_file(base,request['prepare_profile'],'prepare_profile')
    if sha(profile_path)!=APPROVED_PROFILE_SHA:raise PreparationError('PROFILE_NOT_APPROVED','Only the frozen JAK2/6VGL/RXT v1 recipe is supported',2)
    profile=read_json(profile_path)
    if (request['prepare_profile'].get('id'),request['prepare_profile'].get('version'))!=(profile['profile_id'],profile['version']):raise PreparationError('PROFILE_REFERENCE_MISMATCH','Profile id/version differs',2)
    for key in ['mode','target_id','model','protein','reference_ligand','search_box']:
        if request[key]!=profile[key]:raise PreparationError('UNSUPPORTED_SELECTION_OR_POLICY',key,2,expected=profile[key],actual=request[key])
    if request['source_structure'].get('source_id')!=profile['source_id'] or request['chemical_definition'].get('comp_id')!='RXT':raise PreparationError('UNSUPPORTED_SOURCE_OR_COMPONENT','Only 6VGL/RXT is supported',2)
    paths={role:input_file(base,request[key],role) for role,key in [('structure','source_structure'),('sequence','sequence'),('handoff','handoff'),('RXT_CCD','chemical_definition')]}
    for role,source in paths.items():
        if sha(source)!=profile['expected_source_sha256'][role]:raise PreparationError('SOURCE_NOT_SUPPORTED',role,2)
    return request,profile,profile_path,paths


def job_contract(root,request,profile):
    binding=read_json(root/'records/box_binding.json');runtime=read_json(root/'records/runtime_environment.json')
    outputs={role:{**file_record(root,relative),'state':'AVAILABLE'} for role,relative in REQUIRED_OUTPUTS.items()}
    inputs={role:file_record(root,relative) for role,relative in INPUT_NAMES.items()}
    return {'schema_version':'a4s.smoke_job.v1','job_id':request['job_id'],'mode':'smoke_test','model_variant':profile['model_variant'],'target_id':request['target_id'],
      'project_direction':'JAK1/JAK2 project; this module supports only approved JAK2 recipe',
      'source_structure':{'source_id':'6VGL',**inputs['structure']},'inputs':inputs,
      'request':file_record(root,'request.json'),'prepare_profile':{'id':profile['profile_id'],'version':profile['version'],**file_record(root,'profile.json')},
      'selection_authority':{'project_primary_reference_decisions':inputs['handoff'],'modeling_decision':'Approved profile '+profile['profile_id']+' '+profile['version'],'evidence':'records/domain_audit.json'},
      'model':'1','protein':{**profile['protein'],'entity_id':'1','kept_auth_range':profile['keep_auth_range'],'kept_label_range':profile['keep_label_range'],'excluded_auth_range':profile['exclude_only_CIF_expression_tag_auth_range']},
      'reference_ligand':{**profile['reference_ligand'],'atoms':23},
      'termini':{'N':{'residue':'ASP840','template':'NASP','state':'free ammonium N+ (three H)','N_formal_charge':1,'whole_residue_formal_charge':0},
                 'C':{'residue':'GLY1132','template':'CGLY','state':'carboxylate','whole_residue_formal_charge':-1,'new_OXT_origin':'modelled; output O/OXT symmetry mapping recorded'}},
      'PTR':{'residue':'A1007','identity':'PTR','template':'PTR','formal_charge':-2,'physiological_state_validated':False},
      'outputs':outputs,'search_box':{'center':[binding['parameters'][k] for k in ['center_x','center_y','center_z']],
         'size':[binding['parameters'][k] for k in ['size_x','size_y','size_z']],'method':profile['search_box']['method'],'padding':5.0,'unit':'angstrom',
         'coordinate_source_sha256':inputs['structure']['sha256'],'prepared_receptor_sha256':outputs['receptor']['sha256']},
      'tool_versions':runtime,'module_version':MODULE_VERSION,'run_metadata':'run_metadata.json',
      'preparation_status':'PREPARED_PENDING_VALIDATION','ready_for_03':False,'components':{'domain':'PASS','receptor':'PASS','ligand':'PASS_GENERATED','box':'PASS_GENERATED'},'blockers':[],
      'assumptions':['Only CIF-annotated expression tag 825–839 excluded; original source remains incomplete in tag.',
        'Dry rigid isolated JH1 domain, 191 waters excluded as modeling simplification; RXT separated.',
        'NASP free N terminus, CGLY carboxylate C terminus; one modeled OXT and modeled H, no original domain heavy atom moved.',
        'PTR dianion and default residue templates including HIE; physiological protonation not validated.',
        'Reference and starting ligand use experimental co-crystal coordinates; no independent pose prediction.'],
      'warnings':['Meeko graph-symmetric PTR and terminal O/OXT atom name changes are mapped explicitly.',
                  'READY_FOR_03 denotes 02 preparation only; no affinity/activity validation or cross-module integration claim.'],
      'stage03_status':'待联调','vina_read_performed':False,'docking_or_scoring_performed':False}


def run(request_path,out):
    if out.exists():return {'status':'FAIL','exit_code':1,'code':'OUTPUT_EXISTS','message':'Refuse existing output; select a new run directory','result':None,'refused_output':str(out)}
    try:out.mkdir(parents=True,exist_ok=False)
    except OSError as error:return {'status':'FAIL','exit_code':1,'code':'OUTPUT_CREATION_FAILED','message':str(error),'result':None}
    for name in ['original','inputs','outputs','records','records/logs']:(out/name).mkdir(exist_ok=True)
    job={'schema_version':'a4s.smoke_job.v1','job_id':'UNRESOLVED','mode':'smoke_test','preparation_status':'RUNNING','ready_for_03':False,'blockers':[],'outputs':{},'stage03_status':'待联调'}
    metadata={'module_version':MODULE_VERSION,'started_utc':now(),'request_source_path':str(request_path),'output_directory':str(out),'interpreter':sys.executable,
              'implementation_sha256':{p.name:sha(p) for p in sorted(SCRIPTS.glob('*.py'))},'scientific_products_copied':False}
    write_json(out/'job.json',job);write_json(out/'run_metadata.json',metadata)
    python=[sys.executable,*(['-S'] if sys.flags.no_site else []),'-B']
    def step(label,command,allow_blocked=False):
        start=now();env=os.environ.copy();env['PYTHONDONTWRITEBYTECODE']='1'
        result=subprocess.run(command,cwd=out,capture_output=True,text=True,env=env)
        stdout='records/logs/'+label+'.stdout.log';stderr='records/logs/'+label+'.stderr.log'
        (out/stdout).write_text(result.stdout);(out/stderr).write_text(result.stderr)
        record={'label':label,'command':command,'cwd':str(out),'start_utc':start,'end_utc':now(),'exit_code':result.returncode,'stdout':stdout,'stderr':stderr}
        with (out/'records/commands.jsonl').open('a') as h:h.write(json.dumps(record,ensure_ascii=False)+'\n')
        if result.returncode:
            raise PreparationError('SCIENTIFIC_STEP_BLOCKED' if allow_blocked and result.returncode==2 else 'TECHNICAL_STEP_FAILED',label,2 if allow_blocked and result.returncode==2 else 1,step_exit_code=result.returncode,stdout_log=stdout,stderr_log=stderr)
        return result.stdout
    def science(label,action):return step(label,[*python,str(SCRIPTS/'scientific_steps.py'),action,'--root',str(out)],allow_blocked=action=='environment')
    failure=None
    try:
        if sys.flags.optimize:raise PreparationError('OPTIMIZED_MODE_UNSUPPORTED','Scientific assertions must remain enabled; use -B without -O',1)
        if request_path.is_file():
            shutil.copyfile(request_path,out/'request.json');job['request']=file_record(out,'request.json');metadata['request_sha256']=job['request']['sha256']
            try:
                candidate=read_json(request_path).get('job_id')
                if isinstance(candidate,str) and re.fullmatch('[A-Za-z0-9][A-Za-z0-9._-]{0,95}',candidate):job['job_id']=candidate;metadata['job_id']=candidate
            except (ValueError,AttributeError):pass
            write_json(out/'job.json',job);write_json(out/'run_metadata.json',metadata)
        request,profile,profile_path,paths=validate_request(request_path);job['job_id']=request['job_id'];metadata['job_id']=request['job_id']
        shutil.copyfile(request_path,out/'request.json');shutil.copyfile(profile_path,out/'profile.json')
        for role,source in paths.items():shutil.copyfile(source,out/INPUT_NAMES[role])
        write_json(out/'job.json',job);write_json(out/'run_metadata.json',metadata)
        write_json(out/'records/staging.json',{'stage':'before_any_scientific_step','files':[file_record(out,p) for p in ['request.json','profile.json',*INPUT_NAMES.values()]],
                    'prepared_products_present':any((out/p).exists() for p in REQUIRED_OUTPUTS.values()),'raw_input_and_frozen_definition_only':True})
        probe="import importlib.util,json; names=['gemmi','rdkit','meeko','numpy','scipy','tqdm']; print(json.dumps({'missing':[n for n in names if importlib.util.find_spec(n) is None]}))"
        missing=json.loads(step('01-dependency-probe',[*python,'-c',probe]))['missing']
        if missing:raise PreparationError('MISSING_SCIENTIFIC_DEPENDENCY','Missing required packages',1,missing=missing)
        science('02-runtime-environment','environment');science('03-source-and-sequence-audit','audit');science('04-domain-OXT-model','derive')
        step('05-meeko-receptor',[*python,'-m','meeko.cli.mk_prepare_receptor','--read_pdb','inputs/domain_terminal_model.pdb','-o','outputs/receptor','-p','outputs/receptor.pdbqt','-j','outputs/receptor.json','--write_pdb','outputs/receptor_prepared.pdb','--set_template','A:840=NASP,A:1007=PTR,A:1132=CGLY','--debug_fn','records/logs/meeko_receptor_debug.log'])
        science('06-CIF-CCD-reference-and-ligand-SDF','ligand-sdf')
        step('07-meeko-ligand',[*python,'-m','meeko.cli.mk_prepare_ligand','-i','outputs/ligand.sdf','-o','outputs/ligand.pdbqt','--charge_model','gasteiger','--merge_these_atom_types','H','--add_index_map','-v'])
        step('08-meeko-ligand-roundtrip',[*python,'-m','meeko.cli.mk_export','outputs/ligand.pdbqt','-s','outputs/ligand_roundtrip.sdf'])
        science('09-receptor-content','validate-receptor');science('10-ligand-content','validate-ligand');science('11-compute-reference-box','box')
        job=job_contract(out,request,profile);write_json(out/'job.json',job)
        validation=json.loads(step('12-full-scientific-check',[*python,str(SCRIPTS/'check_science.py'),'--root',str(out),'--allow-pending']))
        if validation['status']!='SCIENCE_PASS':raise PreparationError('SCIENCE_VALIDATION_NOT_PASS','Unexpected scientific validation status',1)
        write_json(out/'records/full_science_check.json',validation)
        job['preparation_status']='READY_FOR_03';job['ready_for_03']=True
    except PreparationError as error:failure=error
    except Exception as error:failure=PreparationError('TECHNICAL_EXCEPTION',type(error).__name__+': '+str(error),1)
    code=failure.exit_code if failure else 0;state='BLOCKED' if code==2 else 'FAIL' if code else 'READY_FOR_03'
    if failure:
        job['preparation_status']=state;job['ready_for_03']=False;job['blockers']=[{'code':failure.code,'message':str(failure),**failure.details}]
    metadata.update({'ended_utc':now(),'exit_code':code,'status':state});write_json(out/'run_metadata.json',metadata);write_json(out/'job.json',job)
    result={'status':state,'exit_code':code,'job_id':job['job_id'],'result':str(out),'job':'job.json','stage03_status':'待联调'}
    if failure:result.update({'code':failure.code,'message':str(failure),**failure.details})
    write_json(out/'run_status.json',result);write_manifest(out)
    if not failure:
        integrity=integrity_check(out)
        if integrity['exit_code']:
            result.update({'status':'FAIL','exit_code':1,'code':'FINAL_INTEGRITY_FAILED','issues':integrity['issues']})
            job.update({'preparation_status':'FAIL','ready_for_03':False,'blockers':result['issues']});metadata.update({'status':'FAIL','exit_code':1})
            write_json(out/'job.json',job);write_json(out/'run_metadata.json',metadata);write_json(out/'run_status.json',result);write_manifest(out)
        else:result['integrity_status']='INTEGRITY_PASS'
    return result


class Parser(argparse.ArgumentParser):
    def error(self,message):raise PreparationError('INVALID_CLI',message,2)


if __name__=='__main__':
    created_output=None
    try:
        parser=Parser(description=__doc__);parser.add_argument('--request',required=True,type=Path);parser.add_argument('--out',required=True,type=Path);args=parser.parse_args()
        if not args.out.resolve().exists():created_output=args.out.resolve()
        result=run(args.request.resolve(),args.out.resolve())
    except Exception as error:
        result={'status':'BLOCKED' if isinstance(error,PreparationError) and error.exit_code==2 else 'FAIL','exit_code':error.exit_code if isinstance(error,PreparationError) else 1,
                'code':error.code if isinstance(error,PreparationError) else 'UNHANDLED_TECHNICAL_ERROR','message':str(error),'result':None}
        if created_output is not None and created_output.is_dir():
            try:
                failed=read_json(created_output/'job.json');failed.update({'preparation_status':result['status'],'ready_for_03':False,'blockers':[{'code':result['code'],'message':result['message']}]})
                write_json(created_output/'job.json',failed);result.update({'result':str(created_output),'job_id':failed['job_id']})
                write_json(created_output/'run_status.json',result);write_manifest(created_output)
            except Exception as recovery_error:result['recovery_error']=str(recovery_error)
    print(json.dumps(result,ensure_ascii=False,indent=2));sys.exit(result['exit_code'])
