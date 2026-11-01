#!/usr/bin/env python3
"""Read-only, fixed-domain atom/chemistry validation shared by build and receiver checks."""
from collections import Counter, defaultdict
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

import gemmi
import numpy as np
from meeko import Polymer
from rdkit import Chem

SOURCE_SHA='ba70401d4a19a2e358227d1aa9b09608ab015f9e16abd9aaae7ec2c2745f3c4f'


def category(block,name):
    data=block.get_mmcif_category(name)
    return [{k:(None if v[i] is None or v[i] is False else v[i]) for k,v in data.items()} for i in range(len(next(iter(data.values()))))] if data else []


def coordinates(a):return np.array([float(a[k]) for k in ('Cartn_x','Cartn_y','Cartn_z')])


def pdb_atoms(path):
    result={}
    for model in gemmi.read_structure(str(path)):
        for chain in model:
            for res in chain:
                for atom in res:
                    key=(chain.name,str(res.seqid.num),atom.name)
                    assert key not in result,'Duplicate PDB atom '+str(key)
                    result[key]={'coordinate':np.array([atom.pos.x,atom.pos.y,atom.pos.z]),'element':atom.element.name,'residue':res.name}
    return result


def verify_receptor(root):
    source=root/'original/6VGL.cif';assert hashlib.sha256(source.read_bytes()).hexdigest()==SOURCE_SHA
    block=gemmi.cif.read(str(source)).sole_block();atoms=category(block,'_atom_site.')
    annotations=[r for r in category(block,'_struct_ref_seq_dif.') if r['pdbx_pdb_strand_id']=='A' and r['details']=='expression tag']
    tag={int(r['pdbx_auth_seq_num']) for r in annotations};assert tag==set(range(825,840))
    protein=[a for a in atoms if a['pdbx_PDB_model_num']=='1' and a['auth_asym_id']=='A' and a['label_asym_id']=='D']
    domain=[a for a in protein if int(a['auth_seq_id']) not in tag]
    assert len(domain)==2421 and {int(a['auth_seq_id']) for a in domain}==set(range(840,1133))
    raw_by_res=defaultdict(list)
    for atom in domain:raw_by_res[atom['auth_seq_id']].append({'name':atom['auth_atom_id'],'coordinate':coordinates(atom),'element':atom['type_symbol'],'cif_id':atom['id'],'residue':atom['label_comp_id']})
    original={(a['auth_asym_id'],a['auth_seq_id'],a['auth_atom_id']):a for a in domain}
    experimental=pdb_atoms(root/'inputs/domain_experimental.pdb');assert set(experimental)==set(original),'Experimental domain atom set differs from exact source domain'
    for key,atom in original.items():
        assert experimental[key]['residue']==atom['label_comp_id'] and np.array_equal(experimental[key]['coordinate'],coordinates(atom))
    model=pdb_atoms(root/'inputs/domain_terminal_model.pdb');assert set(model)==set(original)|{('A','1132','OXT')},'Terminal model must contain exactly original domain atoms plus GLY1132 OXT'
    for key,atom in original.items():assert np.array_equal(model[key]['coordinate'],coordinates(atom)) and model[key]['residue']==atom['label_comp_id']
    c=model[('A','1132','C')]['coordinate'];ca=model[('A','1132','CA')]['coordinate'];o=model[('A','1132','O')]['coordinate']
    u=(ca-c)/np.linalg.norm(ca-c);v=(o-c)/np.linalg.norm(o-c);expected_oxt=np.round(c+1.25*(2*np.dot(v,u)*u-v),3)
    assert np.array_equal(model[('A','1132','OXT')]['coordinate'],expected_oxt)
    raw_by_res['1132'].append({'name':'OXT','coordinate':expected_oxt,'element':'O','cif_id':None,'residue':'GLY'})
    prepared=pdb_atoms(root/'outputs/receptor_prepared.pdb')
    lines=(root/'outputs/receptor.pdbqt').read_text().splitlines();assert not any(line.startswith(('ROOT','BRANCH','TORSDOF')) for line in lines)
    pdbqt={};types=Counter();charges=[]
    type_element={'C':'C','A':'C','N':'N','NA':'N','OA':'O','SA':'S','S':'S','P':'P','HD':'H','H':'H'}
    for line in lines:
        if not line.startswith(('ATOM  ','HETATM')):continue
        key=(line[21].strip(),line[22:26].strip(),line[12:16].strip());assert key not in pdbqt
        xyz=np.array([float(line[k:k+8]) for k in (30,38,46)]);charge=float(line[70:76]);kind=line.split()[-1]
        assert np.isfinite(xyz).all() and math.isfinite(charge) and kind in type_element
        pdbqt[key]={'coordinate':xyz,'partial_charge':charge,'element':type_element[kind],'residue':line[17:20].strip()};charges.append(charge);types[kind]+=1
    assert pdbqt
    polymer=Polymer.from_json((root/'outputs/receptor.json').read_text());assert set(polymer.monomers)=={'A:'+str(n) for n in range(840,1133)}
    assert not polymer.log['no_match'] and not polymer.log['no_mol'] and not polymer.log['matched_with_excess_bond']
    mapping=[];hydrogens=[];renames=[];templates=[];heavy_output=set();all_atoms=set();expected_qt=set();formal_total=0
    for key,monomer in polymer.monomers.items():
        chain,number=key.split(':');raw=raw_by_res[number];molecule=monomer.rdkit_mol
        assert monomer.input_resname==raw[0]['residue'],'Residue identity changed: '+key
        assert monomer.raw_rdkit_mol.GetNumAtoms()==len(raw)
        for i,row in enumerate(raw):
            atom=monomer.raw_rdkit_mol.GetAtomWithIdx(i)
            assert atom.GetSymbol()==row['element'] and np.array_equal(np.array(monomer.raw_rdkit_mol.GetConformer().GetAtomPosition(i)),row['coordinate'])
        formal=Chem.GetFormalCharge(molecule);formal_total+=formal
        templates.append({'residue':key,'input_resname':monomer.input_resname,'template':monomer.residue_template_key,'formal_charge':formal})
        for atom in molecule.GetAtoms():
            idx=atom.GetIdx();name=monomer.atom_names[idx];out=(chain,number,name);all_atoms.add(out)
            xyz=np.array(molecule.GetConformer().GetAtomPosition(idx));assert np.isfinite(xyz).all()
            assert out in prepared and prepared[out]['element']==atom.GetSymbol() and prepared[out]['residue']==monomer.input_resname
            assert np.linalg.norm(prepared[out]['coordinate']-xyz)<=0.001
            if atom.GetAtomicNum()>1:
                assert idx in monomer.mapidx_to_raw
                row=raw[monomer.mapidx_to_raw[idx]];assert atom.GetSymbol()==row['element'] and np.array_equal(xyz,row['coordinate'])
                origin='experimental' if row['cif_id'] is not None else 'modelled_OXT'
                entry={'auth_asym_id':chain,'auth_seq_id':number,'residue':row['residue'],'input_atom_name':row['name'],'output_atom_name':name,
                       'cif_atom_site_id':row['cif_id'],'element':row['element'],'origin':origin,'coordinate':xyz.tolist(),'formal_charge_in_template':atom.GetFormalCharge()}
                mapping.append(entry);heavy_output.add(out);expected_qt.add(out)
                if name!=row['name']:renames.append(entry)
            else:
                assert idx not in monomer.mapidx_to_raw
                neighbors=[a.GetSymbol() for a in atom.GetNeighbors()];assert len(neighbors)==1
                merged=neighbors==['C']
                hydrogens.append({'auth_asym_id':chain,'auth_seq_id':number,'residue':monomer.input_resname,'output_atom_name':name,'origin':'modelled_H','coordinate':xyz.tolist(),'merged_in_PDBQT':merged})
                if not merged:expected_qt.add(out)
            if out in pdbqt:
                assert pdbqt[out]['element']==atom.GetSymbol() and pdbqt[out]['residue']==monomer.input_resname
                assert np.linalg.norm(pdbqt[out]['coordinate']-xyz)<=0.001,'PDBQT coordinate mismatch: '+str(out)
    assert set(prepared)==all_atoms,'Prepared PDB atom set disagrees with Meeko JSON'
    assert set(pdbqt)==expected_qt,'PDBQT atom set mismatch: missing='+str(sorted(expected_qt-set(pdbqt)))+' excess='+str(sorted(set(pdbqt)-expected_qt))
    assert len(mapping)==2422 and len([r for r in mapping if r['origin']=='experimental'])==2421
    assert len(heavy_output)==2422 and all(840<=int(k[1])<=1132 for k in pdbqt)
    for residue,template,charge in [('A:840','NASP',0),('A:1132','CGLY',-1),('A:1007','PTR',-2)]:
        monomer=polymer.monomers[residue];assert monomer.residue_template_key==template and Chem.GetFormalCharge(monomer.rdkit_mol)==charge
    n=polymer.monomers['A:840'];nitrogen=n.rdkit_mol.GetAtomWithIdx(n.atom_names.index('N'))
    assert nitrogen.GetFormalCharge()==1 and {n.atom_names[a.GetIdx()] for a in nitrogen.GetNeighbors() if a.GetAtomicNum()==1}=={'H1','H2','H3'}
    end=polymer.monomers['A:1132'];oxygens=[a for a in end.rdkit_mol.GetAtoms() if a.GetSymbol()=='O']
    assert len(oxygens)==2 and sum(a.GetFormalCharge() for a in oxygens)==-1 and all(not any(x.GetAtomicNum()==1 for x in a.GetNeighbors()) for a in oxygens)
    ptr=[r for r in mapping if r['auth_seq_id']=='1007'];assert len(ptr)==16 and all(r['residue']=='PTR' for r in ptr)
    h_merged=sum(r['merged_in_PDBQT'] for r in hydrogens);oxt=next(r for r in mapping if r['origin']=='modelled_OXT')
    return {'status':'PASS','model_variant':'tag_removed_JH1_domain','source_sha256':SOURCE_SHA,'receptor_sha256':hashlib.sha256((root/'outputs/receptor.pdbqt').read_bytes()).hexdigest(),
            'kept_residues':293,'original_heavy_atoms_retained':2421,'original_heavy_atoms_removed_outside_tag':0,'excluded_tag_observed_heavy_atoms':82,
            'added_heavy_atoms':1,'added_heavy_origin':oxt,'modeled_hydrogens_in_prepared_PDB':len(hydrogens),'carbon_H_merged_in_PDBQT':h_merged,
            'PDBQT_retained_polar_H':len(hydrogens)-h_merged,'prepared_PDB_total_atoms':len(prepared),'PDBQT_total_atoms':len(pdbqt),'PDBQT_heavy_atoms':2422,
            'max_experimental_heavy_coordinate_change_angstrom':0.0,'coordinate_and_charge_values_finite':True,'atom_types':dict(types),
            'total_template_formal_charge':formal_total,'PDBQT_partial_charge_sum':sum(charges),'partial_charge_rounding_residual':sum(charges)-formal_total,
            'explicit_templates':{'A:840':'NASP','A:1007':'PTR','A:1132':'CGLY'},'PTR_formal_charge':-2,'N_terminal_N_formal_charge':1,'C_terminal_carboxylate_formal_charge':-1,
            'atom_name_changes_count':len(renames),'atom_name_changes':renames,'name_change_reason':'Meeko matches connectivity/elements, allowing graph-symmetric atom permutations; physical atom identity tracked through mapidx_to_raw, not names alone.',
            'all_residue_templates':templates,'heavy_atom_mapping':mapping,'modeled_hydrogens':hydrogens,
            'physiological_protonation_validated':False,'read_only_validation':True}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--compare-record',type=Path)
    args=parser.parse_args()
    try:
        result=verify_receptor(args.root.resolve())
        if args.compare_record:assert result==json.loads(args.compare_record.read_text()),'Saved validation differs from current production content check'
        print(json.dumps({k:v for k,v in result.items() if k not in ('heavy_atom_mapping','modeled_hydrogens','all_residue_templates','atom_name_changes')},ensure_ascii=False,indent=2))
    except FileNotFoundError as error:
        print(json.dumps({'status':'BLOCKED','error':str(error),'ready_for_03':False}));sys.exit(2)
    except Exception as error:
        print(json.dumps({'status':'FAIL','error':type(error).__name__+': '+str(error),'ready_for_03':False}));sys.exit(1)
