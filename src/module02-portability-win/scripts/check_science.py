#!/usr/bin/env python3
"""Full scientific read-only validation, extracted from accepted R1 checks. Use check_integrity.py for stdlib file checks."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

from receptor_content import verify_receptor

import gemmi
import numpy as np
from rdkit import Chem


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def safe(root,relative):
    path=Path(relative)
    if path.is_absolute() or '..' in path.parts or not str(path) or not (root/path).resolve().is_relative_to(root.resolve()):
        raise ValueError('Path is not confined to package: '+str(relative))
    return root/path


def category(block,name):
    data=block.get_mmcif_category(name)
    if not data:return []
    return [{k:(None if v[i] is None or v[i] is False else v[i]) for k,v in data.items()} for i in range(len(next(iter(data.values()))))]


def check(root,allow_pending=False):
    issues=[];checked=0;missing=[]
    def need(condition,code,**details):
        if not condition:issues.append({'code':code,**details})
    try:
        job=json.loads((root/'job.json').read_text())
        need(job['mode']=='smoke_test','WRONG_MODE')
        need(job['source_structure']['source_id']=='6VGL' and job['target_id']=='JAK2:O60674','OBJECT_ID_MISMATCH')
        source_path=safe(root,job['source_structure']['path'])
        need(sha(source_path)==job['source_structure']['sha256'],'SOURCE_HASH_MISMATCH')
        authority=job['selection_authority']['project_primary_reference_decisions']
        need(sha(safe(root,authority['path']))==authority['sha256']=='41bc876733faad773dcd6b312dcd9795b384bc419609832d2f8fa40771e6475f','HANDOFF_AUTHORITY_HASH_MISMATCH')
        block=gemmi.cif.read(str(source_path)).sole_block();need(category(block,'_entry.')[0]['id']=='6VGL','SOURCE_ENTRY_ID_MISMATCH')
        atoms=category(block,'_atom_site.');selection=job['protein'];model=job['model'];ligand=job['reference_ligand']
        need((model,selection['auth_asym_id'],selection['label_asym_id'])==('1','A','D'),'PROTEIN_SELECTION_MISMATCH')
        need((ligand['comp_id'],ligand['auth_asym_id'],ligand['auth_seq_id'],ligand['label_asym_id'])==('RXT','A','1201','H'),'LIGAND_SELECTION_MISMATCH')
        selected=[a for a in atoms if a['pdbx_PDB_model_num']==model and a['auth_asym_id']==selection['auth_asym_id'] and a['label_asym_id']==selection['label_asym_id']]
        observed_ligand={a['label_atom_id']:a for a in atoms if a['pdbx_PDB_model_num']==model and a['auth_asym_id']==ligand['auth_asym_id'] and a['label_asym_id']==ligand['label_asym_id'] and a['auth_seq_id']==ligand['auth_seq_id'] and a['label_comp_id']==ligand['comp_id']}
        tag_annotations=[r for r in category(block,'_struct_ref_seq_dif.') if r['pdbx_pdb_strand_id']=='A' and r['details']=='expression tag']
        tag_numbers={int(r['pdbx_auth_seq_num']) for r in tag_annotations}
        need(tag_numbers==set(range(825,840)),'TAG_ANNOTATION_MISMATCH')
        need(len(selected)==2503,'SOURCE_PROTEIN_ATOM_COUNT_MISMATCH')
        selected=[a for a in selected if int(a['auth_seq_id']) not in tag_numbers]
        need(job['model_variant']=='tag_removed_JH1_domain' and selection['kept_auth_range']==[840,1132] and selection['kept_label_range']==[16,308] and selection['excluded_auth_range']==[825,839],'DOMAIN_DECISION_MISMATCH')
        need(len(selected)==2421 and len(observed_ligand)==23,'OBJECT_ATOM_COUNT_MISMATCH')
        need(job['stage03_status']=='待联调' and job['vina_read_performed'] is False,'UNSUPPORTED_03_STATUS')
        need(all(a['label_comp_id']=='PTR' for a in selected if a['auth_seq_id']=='1007') and len([a for a in selected if a['auth_seq_id']=='1007'])==16,'PTR1007_IDENTITY_LOST')
        for role,item in job['outputs'].items():
            path=safe(root,item['path'])
            if item['state']=='AVAILABLE':
                need(path.is_file() and sha(path)==item['sha256'],'OUTPUT_HASH_OR_PATH_MISMATCH',role=role);checked+=1
            else:
                missing.append(role);need(not path.exists() and item['sha256'] is None,'BLOCKED_OUTPUT_STATE_CONTRADICTION',role=role)
        if 'receptor' not in missing:
            try:
                content=verify_receptor(root)
                need(content['status']=='PASS','RECEPTOR_CONTENT_FAILURE')
                saved=json.loads((root/'records/receptor_validation.json').read_text())
                need(content==saved,'RECEPTOR_SAVED_EVIDENCE_MISMATCH')
                need(job['components']['receptor']=='PASS','UNVALIDATED_RECEPTOR_DECLARED_AVAILABLE')
                need(job['termini']['N']['template']=='NASP' and job['termini']['C']['template']=='CGLY' and job['PTR']['template']=='PTR' and job['PTR']['formal_charge']==-2,'CHEMICAL_STATE_DECLARATION_MISMATCH')
            except Exception as error:
                need(False,'RECEPTOR_CONTENT_FAILURE',error=type(error).__name__+': '+str(error))
        source_by_id={a['id']:a for a in selected}
        mapping=json.loads((root/'records/extraction.json').read_text())['protein_atom_mapping']
        prepared=gemmi.read_structure(str(safe(root,job['outputs']['selected_protein']['path'])))
        pdb_atoms={atom.serial:(chain,residue,atom) for m in prepared for chain in m for residue in chain for atom in residue}
        need(len(mapping)==len(selected)==len(pdb_atoms),'SELECTED_PROTEIN_MAPPING_INCOMPLETE')
        for row in mapping:
            atom=source_by_id[row['cif_atom_site_id']];chain,residue,output=pdb_atoms[row['pdb_serial']]
            need(chain.name=='A' and str(residue.seqid.num)==atom['auth_seq_id'] and residue.name==atom['label_comp_id'] and output.name==atom['auth_atom_id'],'PROTEIN_ATOM_IDENTITY_MISMATCH')
            need(np.max(np.abs(np.array([output.pos.x,output.pos.y,output.pos.z])-np.array([float(atom[k]) for k in ('Cartn_x','Cartn_y','Cartn_z')])))<1e-7,'PROTEIN_COORDINATE_FRAME_CHANGED')
        def read_sdf(role):
            values=list(Chem.SDMolSupplier(str(safe(root,job['outputs'][role]['path'])),removeHs=False))
            if len(values)!=1 or values[0] is None:raise ValueError('Expected one SDF molecule for '+role)
            return values[0]
        reference=read_sdf('reference');working=read_sdf('ligand_sdf')
        need(Chem.MolToSmiles(Chem.RemoveHs(reference),isomericSmiles=True)==Chem.MolToSmiles(Chem.RemoveHs(working),isomericSmiles=True),'LIGAND_CHEMICAL_IDENTITY_MISMATCH')
        need(reference.GetNumHeavyAtoms()==23 and Chem.GetFormalCharge(reference)==0,'LIGAND_STATE_MISMATCH')
        coordinates=reference.GetConformer().GetPositions()
        reference_map=json.loads((root/'records/ligand_sdf_preparation.json').read_text())['atom_mapping']
        for row in reference_map:
            atom=observed_ligand[row['cif_atom_id']];index=row['reference_index_1based']-1
            need(np.max(np.abs(coordinates[index]-np.array([float(atom[k]) for k in ('Cartn_x','Cartn_y','Cartn_z')])))<1e-7,'REFERENCE_COORDINATE_FRAME_CHANGED')
        ligand_lines=safe(root,job['outputs']['ligand']['path']).read_text().splitlines()
        remark=next(line[len('REMARK SMILES '):] for line in ligand_lines if line.startswith('REMARK SMILES ') and not line.startswith('REMARK SMILES IDX '))
        molecule=Chem.MolFromSmiles(remark)
        need(molecule is not None and Chem.MolToSmiles(molecule,isomericSmiles=True)==Chem.MolToSmiles(Chem.RemoveHs(reference),isomericSmiles=True),'LIGAND_PDBQT_IDENTITY_MISMATCH')
        index_values=[]
        for line in ligand_lines:
            if line.startswith('REMARK INDEX MAP '):index_values.extend(int(x) for x in line[len('REMARK INDEX MAP '):].split())
        need(len(index_values)%2==0 and bool(index_values),'MISSING_LIGAND_INDEX_MAP')
        indices=dict(zip(index_values[::2],index_values[1::2]))
        pdbqt_atoms={int(line[6:11]):line for line in ligand_lines if line.startswith(('ATOM  ','HETATM'))}
        need(len(pdbqt_atoms)==24,'LIGAND_PDBQT_ATOM_COUNT_MISMATCH')
        for line in pdbqt_atoms.values():
            need(np.isfinite([float(line[i:i+8]) for i in (30,38,46)]).all() and math.isfinite(float(line[70:76])),'NONFINITE_LIGAND_PARAMETER')
            need(line.split()[-1] in {'C','A','N','NA','HD'},'UNEXPECTED_LIGAND_ATOM_TYPE')
        for row in reference_map:
            index=row['reference_index_1based'];line=pdbqt_atoms[indices[index]]
            xyz=np.array([float(line[i:i+8]) for i in (30,38,46)])
            need(np.isfinite(xyz).all() and math.isfinite(float(line[70:76])),'NONFINITE_LIGAND_PARAMETER')
            need(np.max(np.abs(xyz-coordinates[index-1]))<=0.001,'LIGAND_PDBQT_COORDINATE_FRAME_CHANGED')
        parameters={}
        for line in safe(root,job['outputs']['vina_box']['path']).read_text().splitlines():
            key,value=line.split('=',1);key=key.strip();need(key not in parameters,'DUPLICATE_BOX_KEY');parameters[key]=float(value)
        center=(np.min(coordinates,axis=0)+np.max(coordinates,axis=0))/2
        size=np.max(coordinates,axis=0)-np.min(coordinates,axis=0)+2*job['search_box']['padding']
        need(job['search_box']['unit']=='angstrom','BOX_UNIT_MISMATCH')
        need(np.allclose(center,job['search_box']['center'],rtol=0,atol=1e-6) and np.allclose(size,job['search_box']['size'],rtol=0,atol=1e-6),'JOB_BOX_MISMATCH')
        need(set(parameters)=={'center_x','center_y','center_z','size_x','size_y','size_z'},'BOX_KEYS_MISMATCH')
        need(np.allclose([parameters[k] for k in ('center_x','center_y','center_z')],center,rtol=0,atol=1e-6) and np.allclose([parameters[k] for k in ('size_x','size_y','size_z')],size,rtol=0,atol=1e-6),'BOX_FILE_MISMATCH')
        need(all(math.isfinite(x) for x in parameters.values()),'NONFINITE_BOX_VALUE')
        binding=json.loads((root/'records/box_binding.json').read_text())
        need(binding['source_structure']==job['source_structure']['path'] and binding['source_structure_sha256']==job['source_structure']['sha256'],'BOX_SOURCE_BINDING_MISMATCH')
        need(binding['reference_sdf']==job['outputs']['reference']['path'] and binding['reference_sdf_sha256']==job['outputs']['reference']['sha256'],'BOX_REFERENCE_BINDING_MISMATCH')
        need(binding['selected_protein_pdb']==job['outputs']['selected_protein']['path'] and binding['selected_protein_sha256']==job['outputs']['selected_protein']['sha256'],'BOX_PROTEIN_BINDING_MISMATCH')
        need(binding['prepared_receptor_pdbqt_sha256']==job['search_box']['prepared_receptor_sha256']==job['outputs']['receptor']['sha256'],'BOX_PREPARED_RECEPTOR_BINDING_MISMATCH')
        need(binding['unit']==job['search_box']['unit'] and binding['padding_angstrom']==job['search_box']['padding'],'BOX_BINDING_METHOD_MISMATCH')
        need(set(binding['parameters'])==set(parameters) and all(math.isclose(binding['parameters'][key],value,rel_tol=0,abs_tol=1e-6) for key,value in parameters.items()),'BOX_BINDING_PARAMETER_MISMATCH')
        pending=allow_pending and job['preparation_status']=='PREPARED_PENDING_VALIDATION'
        blocked=job['preparation_status']=='BLOCKED'
        if blocked:
            need(job['ready_for_03'] is False and 'receptor' in missing and bool(job['blockers']),'BLOCKED_STATE_NOT_HONEST')
        elif pending:
            need(not missing and job['ready_for_03'] is False,'PENDING_STATE_MISMATCH')
        else:
            need(job['preparation_status']=='READY_FOR_03' and not missing and job['ready_for_03'] is True,'UNSUPPORTED_READY_STATE')
    except Exception as error:
        issues.append({'code':'CHECK_ERROR','error':type(error).__name__+': '+str(error)})
        blocked=True;pending=False
    status='FAIL' if issues else 'BLOCKED' if blocked else 'SCIENCE_PASS' if pending else 'READY_FOR_03'
    return {'status':status,'exit_code':1 if issues else 2 if blocked else 0,'read_only':True,
            'scientific_content':'FAIL' if issues else 'PASS','checked_files':checked,
            'missing_components':missing,'issues':issues,'ready_for_03':status=='READY_FOR_03','vina_read_performed':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--allow-pending',action='store_true',help='Internal pre-promotion validation; success is SCIENCE_PASS, not READY.');args=parser.parse_args();result=check(args.root.resolve(),args.allow_pending);print(json.dumps(result,ensure_ascii=False,indent=2));sys.exit(result['exit_code'])
