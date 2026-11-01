#!/usr/bin/env python3
"""Extracted accepted domain/ligand functions; explicit run root, no old project imports."""
from collections import Counter,defaultdict
import hashlib,json,math,platform,sys
from pathlib import Path
import gemmi,meeko
import numpy as np
from rdkit import Chem,rdBase
from rdkit.Chem import rdMolDescriptors
SOURCE='original/6VGL.cif'
SOURCE_SHA='ba70401d4a19a2e358227d1aa9b09608ab015f9e16abd9aaae7ec2c2745f3c4f'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def save(path,value):
    with path.open('x') as h:json.dump(value,h,ensure_ascii=False,indent=2);h.write('\n')


def rows(block,name):
    data=block.get_mmcif_category(name)
    return [{k:(None if v[i] is None or v[i] is False else v[i]) for k,v in data.items()} for i in range(len(next(iter(data.values()))))] if data else []


def xyz(a):return np.array([float(a[k]) for k in ('Cartn_x','Cartn_y','Cartn_z')])


def selected_source(root):
    source=root/'original/6VGL.cif';assert sha(source)==SOURCE_SHA,'Source hash differs; do not apply fixed numbering'
    block=gemmi.cif.read(str(source)).sole_block();assert rows(block,'_entry.')[0]['id']=='6VGL'
    atoms=rows(block,'_atom_site.')
    protein=[a for a in atoms if a['pdbx_PDB_model_num']=='1' and a['auth_asym_id']=='A' and a['label_asym_id']=='D']
    ligand=[a for a in atoms if a['pdbx_PDB_model_num']=='1' and a['auth_asym_id']=='A' and a['label_asym_id']=='H' and a['auth_seq_id']=='1201' and a['label_comp_id']=='RXT']
    return block,protein,ligand


def pdb_line(atom,serial):
    x,y,z=xyz(atom)
    return f"{atom['group_PDB']:<6}{serial:5d} {atom['auth_atom_id']:>4} {atom['auth_comp_id']:>3} A{int(atom['auth_seq_id']):4d}    {x:8.3f}{y:8.3f}{z:8.3f}{float(atom['occupancy']):6.2f}{float(atom['B_iso_or_equiv']):6.2f}          {atom['type_symbol']:>2}  \n"


def audit(root):
    block,protein,ligand=selected_source(root)
    annotation=[r for r in rows(block,'_struct_ref_seq_dif.') if r['pdbx_pdb_strand_id']=='A' and r['details']=='expression tag']
    tag_numbers={int(r['pdbx_auth_seq_num']) for r in annotation};assert tag_numbers==set(range(825,840))
    mapping=[r for r in rows(block,'_struct_ref_seq.') if r['pdbx_strand_id']=='A']
    assert len(mapping)==1
    m=mapping[0];assert (m['seq_align_beg'],m['seq_align_end'],m['db_align_beg'],m['db_align_end'],m['pdbx_db_accession'])==('16','308','840','1132','O60674')
    tag=[a for a in protein if int(a['auth_seq_id']) in tag_numbers]
    domain=[a for a in protein if int(a['auth_seq_id']) not in tag_numbers]
    assert len(protein)==2503 and len(tag)==82 and len(domain)==2421 and len(ligand)==23
    assert {int(a['auth_seq_id']) for a in tag}==set(range(828,840))
    assert {int(a['auth_seq_id']) for a in domain}==set(range(840,1133))
    assert all(int(a['label_seq_id'])==int(a['auth_seq_id'])-824 for a in domain)
    assert all(a['type_symbol'] not in ('H','D') and a['label_alt_id'] is None and np.isfinite(xyz(a)).all() for a in protein+ligand)
    residues={int(a['auth_seq_id']):a['label_comp_id'] for a in domain}
    components={r['id']:r for r in rows(block,'_chem_comp.')}
    assert components['PTR']['name']=='O-PHOSPHOTYROSINE' and gemmi.find_tabulated_residue('PTR').one_letter_code.upper()=='Y'
    seq=''.join('Y' if residues[n]=='PTR' else gemmi.find_tabulated_residue(residues[n]).one_letter_code for n in range(840,1133))
    fasta=''.join(line.strip() for line in (root/'original/JAK2_O60674.fasta').read_text().splitlines() if not line.startswith('>'))
    assert len(fasta)==1132 and seq==fasta[839:1132]
    component_atoms=rows(block,'_chem_comp_atom.');missing=[]
    for number,name in sorted(residues.items()):
        expected={r['atom_id'] for r in component_atoms if r['comp_id']==name and r['type_symbol'] not in ('H','D') and r['atom_id']!='OXT'}
        observed={a['label_atom_id'] for a in domain if int(a['auth_seq_id'])==number}
        assert expected
        if expected!=observed:missing.append({'auth_seq_id':number,'residue':name,'missing':sorted(expected-observed),'excess':sorted(observed-expected)})
    assert not missing,missing
    unobserved=[r for r in rows(block,'_pdbx_unobs_or_zero_occ_residues.') if r['auth_asym_id']=='A' and r['label_asym_id']=='D']
    missing_atoms=[r for r in rows(block,'_pdbx_unobs_or_zero_occ_atoms.') if r['auth_asym_id']=='A' and r['label_asym_id']=='D']
    assert {int(r['auth_seq_id']) for r in unobserved}=={825,826,827}
    assert len(missing_atoms)==29 and all(int(r['auth_seq_id']) in tag_numbers for r in missing_atoms)
    assert len([a for a in domain if a['auth_seq_id']=='1007' and a['label_comp_id']=='PTR'])==16
    water=[a for a in rows(block,'_atom_site.') if a['pdbx_PDB_model_num']=='1' and a['auth_asym_id']=='A' and a['label_comp_id']=='HOH']
    coordinates=np.array([xyz(a) for a in ligand]);center=(coordinates.min(0)+coordinates.max(0))/2;size=coordinates.max(0)-coordinates.min(0)+10
    distance=min(float(np.linalg.norm(xyz(a)-xyz(b))) for a in tag for b in ligand)
    record={'scan_execution':'SUCCESS','anomalies_detected':True,'input_suitable_for_authorized_derivation':True,
            'source_sha256':SOURCE_SHA,'model':'1','protein':{'auth_asym_id':'A','label_asym_id':'D'},
            'expression_tag_annotation':annotation,'uniprot_mapping':mapping,'tag_auth_range':[825,839],
            'excluded_observed_residues':12,'excluded_observed_heavy_atoms':82,'tag_unobserved_residues':unobserved,
            'source_unobserved_atom_annotations':missing_atoms,'missing_atoms_all_in_excluded_tag':True,
            'kept_auth_range':[840,1132],'kept_label_range':[16,308],'kept_residues':293,'kept_original_heavy_atoms':2421,
            'kept_sequence_matches_O60674_FASTA':True,'sequence_comparison_PTR_parent':'TYR; sequence comparison only, model remains PTR',
            'PTR_parent_evidence':'Source names PTR O-PHOSPHOTYROSINE; Gemmi standard residue table maps PTR to y/Y. Source _chem_comp has no mon_nstd_parent_comp_id field.',
            'kept_sequence':seq,'domain_internal_missing_heavy_except_OXT':missing,'domain_internal_unobserved_residues':[],
            'OXT1132_present':False,'PTR1007_heavy_atoms':16,'water_excluded_count':len(water),
            'tag_min_distance_to_RXT_angstrom':distance,'distance_is_not_proof_of_biological_irrelevance':True,
            'box_recomputed':{'center':center.tolist(),'size':size.tolist(),'padding_each_face':5.0,'unit':'angstrom'},
            'warnings':['Source remains incomplete in expression tag; authorized derived model excludes only annotated tag.',
                        'Missing terminal OXT requires explicit modeled atom; source is not relabeled complete.',
                        'Dry isolated-domain model and chosen chemical states are smoke assumptions, not physiological validation.']}
    save(root/'records/domain_audit.json',record)
    save(root/'records/ligand_component_definition_from_structure.json',{'atoms':[r for r in rows(block,'_chem_comp_atom.') if r['comp_id']=='RXT'],'bonds':[r for r in rows(block,'_chem_comp_bond.') if r['comp_id']=='RXT'],'components':[r for r in rows(block,'_chem_comp.') if r['id']=='RXT']})
    print(json.dumps({k:v for k,v in record.items() if k not in ('expression_tag_annotation','source_unobserved_atom_annotations','kept_sequence')},ensure_ascii=False,indent=2))


def derive(root):
    audit_record=json.loads((root/'records/domain_audit.json').read_text());assert audit_record['input_suitable_for_authorized_derivation']
    block,protein,ligand=selected_source(root);tag_numbers={int(r['pdbx_auth_seq_num']) for r in audit_record['expression_tag_annotation']}
    domain=[a for a in protein if int(a['auth_seq_id']) not in tag_numbers];tag=[a for a in protein if int(a['auth_seq_id']) in tag_numbers]
    atom_mapping=[];lines=[]
    for serial,atom in enumerate(domain,1):
        line=pdb_line(atom,serial);assert np.array_equal(xyz(atom),[float(line[k:k+8]) for k in (30,38,46)])
        lines.append(line);atom_mapping.append({'pdb_serial':serial,'cif_atom_site_id':atom['id'],'auth_asym_id':'A','label_asym_id':'D','auth_seq_id':atom['auth_seq_id'],'label_seq_id':atom['label_seq_id'],'residue':atom['label_comp_id'],'atom_name':atom['auth_atom_id'],'element':atom['type_symbol'],'coordinate':xyz(atom).tolist(),'origin':'experimental'})
    with (root/'inputs/domain_experimental.pdb').open('x') as h:h.writelines(lines);h.write('TER\nEND\n')
    end={a['auth_atom_id']:xyz(a) for a in domain if a['auth_seq_id']=='1132'}
    c,ca,o=(end[k] for k in ('C','CA','O'));axis=(ca-c)/np.linalg.norm(ca-c);u=(o-c)/np.linalg.norm(o-c)
    direction=2*np.dot(u,axis)*axis-u
    modeled=c+1.25*direction;rounded=np.round(modeled,3)
    oxt={**domain[-1],'group_PDB':'ATOM','auth_atom_id':'OXT','label_atom_id':'OXT','type_symbol':'O','Cartn_x':str(rounded[0]),'Cartn_y':str(rounded[1]),'Cartn_z':str(rounded[2]),'occupancy':'1.00','B_iso_or_equiv':'0.00'}
    with (root/'inputs/domain_terminal_model.pdb').open('x') as h:h.writelines(lines);h.write(pdb_line(oxt,2422));h.write('TER\nEND\n')
    templates_path=Path(meeko.__file__).parent/'data/residue_chem_templates.json';data=json.loads(templates_path.read_text());plan={};params=Chem.SmilesParserParams();params.removeHs=False
    for residue,key in [('A:840','NASP'),('A:1132','CGLY'),('A:1007','PTR')]:
        template=data['residue_templates'][key];m=Chem.MolFromSmiles(template['smiles'],params)
        plan[residue]={'template':key,'definition':template,'residue_formal_charge':Chem.GetFormalCharge(m),'atoms':[{'name':template['atom_name'][a.GetIdx()],'element':a.GetSymbol(),'formal_charge':a.GetFormalCharge()} for a in m.GetAtoms()]}
    record={'model_variant':'tag_removed_JH1_domain','protein_atom_mapping':atom_mapping,
            'excluded_atoms':[{'cif_atom_site_id':a['id'],'auth_seq_id':a['auth_seq_id'],'label_seq_id':a['label_seq_id'],'residue':a['label_comp_id'],'atom_name':a['auth_atom_id'],'reason':'CIF expression tag annotation'} for a in tag],
            'kept_original_heavy_atoms':2421,'kept_residues':293,'original_heavy_coordinate_max_change_angstrom':0.0,
            'added_atoms':[{'auth_asym_id':'A','auth_seq_id':'1132','residue':'GLY','atom_name':'OXT','element':'O','origin':'modelled','coordinate':rounded.tolist(),'unrounded_coordinate':modeled.tolist(),'formal_charge_by_CGLY_template':-1}],
            'OXT_method':{'description':'Planar geometric construction: reflect unit C→O vector around unit C→CA axis, use C–OXT=1.25 Å; existing CA/C/O unchanged; round new coordinate to PDB 0.001 Å.',
                          'equation':'u=(CA-C)/|CA-C|; v=(O-C)/|O-C|; OXT=C+1.25*(2*(v·u)*u-v)',
                          'requested_bond_length_angstrom':1.25,'written_bond_length_angstrom':float(np.linalg.norm(rounded-c)),
                          'CA_C_OXT_angle_deg':float(np.degrees(np.arccos(np.dot(axis,(rounded-c)/np.linalg.norm(rounded-c))))),
                          'O_C_OXT_angle_deg':float(np.degrees(np.arccos(np.dot(u,(rounded-c)/np.linalg.norm(rounded-c))))),
                          'geometry_not_experimental_measurement':True,'new_atom_PDB_occupancy_and_B':'1.00 / 0.00 are modeling placeholders, not measured statistics'},
            'terminal_scheme':{'N':'free ammonium N+ with H1/H2/H3 using NASP at ASP840; residue net 0 because sidechain ASP is -1',
                               'C':'carboxylate CGLY1132; OXT formal -1; no terminal hydroxyl H'},
            'template_file_sha256':sha(templates_path),'explicit_templates':plan,'PTR_preserved':True,'physiological_protonation_validated':False}
    record['reference_ligand']={'comp_id':'RXT','auth_asym_id':'A','auth_seq_id':'1201','label_asym_id':'H','atoms':len(ligand)}
    record['ligand_atom_site_rows']=ligand
    save(root/'records/extraction.json',record)
    environment={'python':sys.executable,'version':platform.python_version(),'architecture':platform.machine(),'rdkit':rdBase.rdkitVersion,'meeko':meeko.__version__,'gemmi':gemmi.__version__,'numpy':np.__version__,'dependencies_added':False}
    save(root/'records/environment.json',environment)
    print(json.dumps({k:v for k,v in record.items() if k not in ('protein_atom_mapping','excluded_atoms','explicit_templates')},ensure_ascii=False,indent=2))


def identity(mol):
    return Chem.MolToSmiles(Chem.RemoveHs(mol),canonical=True,isomericSmiles=True)


def prepare_ligand_sdf(root):
    extraction=json.loads((root/'records/extraction.json').read_text())
    definition=json.loads((root/'records/ligand_component_definition_from_structure.json').read_text())
    ccd=gemmi.cif.read(str(root/'original/RXT_CCD.cif')).sole_block()
    ccd_atoms={r['atom_id']:r for r in rows(ccd,'_chem_comp_atom.')}
    ccd_bonds=rows(ccd,'_chem_comp_bond.')
    source_atoms=definition['atoms'];source_bonds=definition['bonds']
    assert {r['atom_id'] for r in source_atoms}==set(ccd_atoms)
    for atom in source_atoms:
        other=ccd_atoms[atom['atom_id']]
        assert atom['type_symbol']==other['type_symbol'] and atom['pdbx_stereo_config']==other['pdbx_stereo_config']
    def bonds_key(bonds):
        return {(tuple(sorted((r['atom_id_1'],r['atom_id_2']))),r['value_order'].upper(),r.get('pdbx_aromatic_flag')) for r in bonds}
    assert bonds_key(source_bonds)==bonds_key(ccd_bonds)
    rw=Chem.RWMol();indices={}
    for atom_definition in source_atoms:
        atom=Chem.Atom(atom_definition['type_symbol']);atom.SetFormalCharge(int(ccd_atoms[atom_definition['atom_id']]['charge']))
        atom.SetProp('cif_atom_id',atom_definition['atom_id'])
        indices[atom_definition['atom_id']]=rw.AddAtom(atom)
    bond_types={'SING':Chem.BondType.SINGLE,'DOUB':Chem.BondType.DOUBLE,'TRIP':Chem.BondType.TRIPLE,'AROM':Chem.BondType.AROMATIC}
    for bond in source_bonds:rw.AddBond(indices[bond['atom_id_1']],indices[bond['atom_id_2']],bond_types[bond['value_order'].upper()])
    full=rw.GetMol();Chem.SanitizeMol(full)
    reference=Chem.RemoveHs(full)
    coordinates={r['label_atom_id']:[float(r[k]) for k in ('Cartn_x','Cartn_y','Cartn_z')] for r in extraction['ligand_atom_site_rows']}
    assert set(coordinates)=={a.GetProp('cif_atom_id') for a in reference.GetAtoms()}
    conformer=Chem.Conformer(reference.GetNumAtoms());conformer.Set3D(True)
    atom_mapping=[]
    for atom in reference.GetAtoms():
        name=atom.GetProp('cif_atom_id');conformer.SetAtomPosition(atom.GetIdx(),coordinates[name])
        atom_mapping.append({'reference_index_1based':atom.GetIdx()+1,'cif_atom_id':name,'element':atom.GetSymbol(),'source_coordinate':coordinates[name]})
    reference.AddConformer(conformer)
    Chem.AssignStereochemistryFrom3D(reference)
    stereocenters=[]
    for atom in reference.GetAtoms():
        expected=ccd_atoms[atom.GetProp('cif_atom_id')]['pdbx_stereo_config']
        if expected in ('R','S'):
            actual=atom.GetProp('_CIPCode') if atom.HasProp('_CIPCode') else None
            assert actual==expected,(atom.GetProp('cif_atom_id'),expected,actual)
            stereocenters.append({'cif_atom_id':atom.GetProp('cif_atom_id'),'expected_CCD':expected,'from_experimental_3D':actual})
    component=rows(ccd,'_chem_comp.')[0]
    assert Chem.GetFormalCharge(reference)==int(component['pdbx_formal_charge'])==0
    assert rdMolDescriptors.CalcMolFormula(reference)=='C17H18N6'
    descriptors=rows(ccd,'_pdbx_chem_comp_descriptor.')
    smiles_checks=[]
    for descriptor in descriptors:
        if descriptor['type']=='SMILES_CANONICAL':
            expected=Chem.MolFromSmiles(descriptor['descriptor'])
            assert identity(expected)==identity(reference)
            smiles_checks.append({'program':descriptor['program'],'canonical_identity_matches':True})
    reference.SetProp('_Name','RXT_6VGL_model1_authA1201_labelH_experimental_reference')
    reference.SetProp('role','EXPERIMENTAL_COCRYSTAL_REFERENCE; not an independently predicted pose')
    reference.SetProp('source_structure_sha256',SOURCE_SHA)
    reference.SetProp('chemical_state','RXT official CCD neutral state; physiological protonation not validated')
    with Chem.SDWriter(str(root/'outputs/reference.sdf')) as writer:writer.write(reference)
    ligand=Chem.AddHs(reference,addCoords=True)
    assert identity(ligand)==identity(reference)
    np.testing.assert_array_equal(ligand.GetConformer().GetPositions()[:reference.GetNumAtoms()],reference.GetConformer().GetPositions())
    ligand.SetProp('_Name','RXT_6VGL_smoke_ligand')
    ligand.SetProp('hydrogen_method','RDKit AddHs(addCoords=True); heavy coordinates retained; no minimization or embedding')
    with Chem.SDWriter(str(root/'outputs/ligand.sdf')) as writer:writer.write(ligand)
    record={'status':'PASS','source_instance':extraction['reference_ligand'],'reference':'outputs/reference.sdf','ligand':'outputs/ligand.sdf',
            'graph_source':'6VGL chemical-component atom/bond table; formal charges cross-checked with official RXT CCD',
            'structure_and_CCD_full_graph_match':True,'formula':rdMolDescriptors.CalcMolFormula(reference),
            'bond_order_token_normalization':'Source CIF uses lowercase sing/doub/trip; official CCD uses uppercase. Compared case-insensitively; no chemical bond-order change.',
            'canonical_isomeric_smiles':identity(reference),'formal_charge':Chem.GetFormalCharge(reference),
            'reference_heavy_atoms':reference.GetNumHeavyAtoms(),'ligand_atoms':ligand.GetNumAtoms(),
            'hydrogens_added':ligand.GetNumAtoms()-reference.GetNumAtoms(),'stereocenters':stereocenters,
            'CCD_descriptor_comparisons':smiles_checks,'coordinates_finite':bool(np.isfinite(ligand.GetConformer().GetPositions()).all()),
            'max_heavy_coordinate_change_angstrom':0.0,'physiological_protonation_validated':False,
            'geometry_optimization_or_embedding_performed':False,'atom_mapping':atom_mapping}
    save(root/'records/ligand_sdf_preparation.json',record)
    print(json.dumps({k:v for k,v in record.items() if k!='atom_mapping'},ensure_ascii=False,indent=2))


def read_one_sdf(path):
    molecules=list(Chem.SDMolSupplier(str(path),removeHs=False,sanitize=True,strictParsing=True))
    if len(molecules)!=1 or molecules[0] is None:raise ValueError('Expected one valid SDF molecule: '+str(path))
    return molecules[0]


def index_pairs(lines,prefix):
    values=[]
    for line in lines:
        if line.startswith(prefix+' '):values.extend(int(x) for x in line[len(prefix):].split())
    assert values and len(values)%2==0
    result=dict(zip(values[::2],values[1::2]))
    assert len(result)*2==len(values) and len(set(result.values()))==len(result)
    return result


def validate_ligand(root):
    reference=read_one_sdf(root/'outputs/reference.sdf')
    ligand=read_one_sdf(root/'outputs/ligand.sdf')
    exported=read_one_sdf(root/'outputs/ligand_roundtrip.sdf')
    assert identity(reference)==identity(ligand)==identity(exported)
    assert all(Chem.GetFormalCharge(m)==0 and m.GetNumHeavyAtoms()==23 for m in (reference,ligand,exported))
    assert all(m.GetConformer().Is3D() and np.isfinite(m.GetConformer().GetPositions()).all() for m in (reference,ligand,exported))
    sdf_record=json.loads((root/'records/ligand_sdf_preparation.json').read_text())
    extraction=json.loads((root/'records/extraction.json').read_text())
    source_coordinates={a['label_atom_id']:np.array([float(a[k]) for k in ('Cartn_x','Cartn_y','Cartn_z')]) for a in extraction['ligand_atom_site_rows']}
    lines=(root/'outputs/ligand.pdbqt').read_text().splitlines()
    input_to_serial=index_pairs(lines,'REMARK INDEX MAP')
    smiles_to_serial=index_pairs(lines,'REMARK SMILES IDX');serial_to_export={serial:index-1 for index,serial in smiles_to_serial.items()}
    pdbqt={int(line[6:11]):{'coordinate':np.array([float(line[30:38]),float(line[38:46]),float(line[46:54])]),'charge':float(line[70:76]),'type':line.split()[-1]}
           for line in lines if line.startswith(('ATOM  ','HETATM'))}
    assert set(pdbqt)==set(input_to_serial.values())
    assert all(np.isfinite(r['coordinate']).all() and math.isfinite(r['charge']) for r in pdbqt.values())
    mapping=[];input_to_export={}
    for row in sdf_record['atom_mapping']:
        index=row['reference_index_1based']-1;serial=input_to_serial[index+1];export_index=serial_to_export[serial]
        source=source_coordinates[row['cif_atom_id']]
        assert np.max(np.abs(reference.GetConformer().GetPositions()[index]-source))<1e-7
        assert np.max(np.abs(ligand.GetConformer().GetPositions()[index]-source))<1e-7
        pdbqt_delta=float(np.linalg.norm(pdbqt[serial]['coordinate']-source))
        export_delta=float(np.linalg.norm(exported.GetConformer().GetPositions()[export_index]-source))
        assert pdbqt_delta<=0.001 and export_delta<=0.001
        input_to_export[index]=export_index
        mapping.append({**row,'ligand_index_1based':index+1,'pdbqt_serial':serial,'export_index_1based':export_index+1,
                        'max_source_to_pdbqt_displacement_angstrom':pdbqt_delta,'max_source_to_export_displacement_angstrom':export_delta})
    for bond in ligand.GetBonds():
        first,second=bond.GetBeginAtomIdx(),bond.GetEndAtomIdx()
        if first in input_to_export and second in input_to_export:
            other=exported.GetBondBetweenAtoms(input_to_export[first],input_to_export[second])
            assert other is not None and other.GetBondType()==bond.GetBondType() and other.GetIsAromatic()==bond.GetIsAromatic()
    dropped=set(range(1,ligand.GetNumAtoms()+1))-set(input_to_serial)
    carbon_hydrogens={a.GetIdx()+1 for a in ligand.GetAtoms() if a.GetAtomicNum()==1 and a.GetNeighbors()[0].GetAtomicNum()==6}
    assert dropped==carbon_hydrogens
    assert len(pdbqt)==24 and len(dropped)==17
    charge_sum=sum(r['charge'] for r in pdbqt.values());assert abs(charge_sum)<=0.02
    record={'status':'PASS','source_structure_sha256':SOURCE_SHA,'source_instance':extraction['reference_ligand'],
            'reference_heavy_atoms':23,'ligand_atoms':ligand.GetNumAtoms(),'pdbqt_atoms':len(pdbqt),
            'pdbqt_retained_polar_hydrogens':1,'merged_carbon_bound_hydrogens':len(dropped),
            'formal_charge':0,'partial_charge_sum':charge_sum,'canonical_isomeric_smiles':identity(ligand),
            'identity_stereochemistry_and_mapped_heavy_bonds_match':True,'stereocenters':sdf_record['stereocenters'],
            'max_heavy_coordinate_displacement_angstrom':max(r['max_source_to_export_displacement_angstrom'] for r in mapping),
            'coordinates_and_charges_finite':True,'geometry_optimization_or_embedding_performed':False,
            'reference_pose_used_for_smoke_input':True,'independent_pose_prediction_accuracy_assessed':False,'atom_mapping':mapping}
    save(root/'records/ligand_validation.json',record)
    print(json.dumps({k:v for k,v in record.items() if k!='atom_mapping'},ensure_ascii=False,indent=2))


def make_box(root,padding=5.0):
    assert padding>0
    reference=read_one_sdf(root/'outputs/reference.sdf')
    coordinates=reference.GetConformer().GetPositions();assert np.isfinite(coordinates).all()
    lower,upper=np.min(coordinates,axis=0),np.max(coordinates,axis=0)
    center=(lower+upper)/2;size=upper-lower+2*padding
    parameters=dict(zip(('center_x','center_y','center_z','size_x','size_y','size_z'),np.concatenate((center,size)).tolist()))
    with (root/'outputs/vina_box.txt').open('x') as handle:
        for key,value in parameters.items():handle.write(f'{key} = {value:.6f}\n')
    record={'status':'BOUND_TO_PREPARED_RECEPTOR','method':'Axis-aligned bounding box of 23 experimental RXT heavy atoms plus padding on each face',
            'padding_angstrom':padding,'parameters':parameters,'unit':'angstrom','reference_sdf':'outputs/reference.sdf','reference_sdf_sha256':sha(root/'outputs/reference.sdf'),
            'source_structure':SOURCE,'source_structure_sha256':SOURCE_SHA,'selected_protein_pdb':'inputs/domain_experimental.pdb',
            'selected_protein_sha256':sha(root/'inputs/domain_experimental.pdb'),'prepared_receptor_pdbqt_sha256':sha(root/'outputs/receptor.pdbqt'),
            'reference_min_xyz':lower.tolist(),'reference_max_xyz':upper.tolist(),'coordinate_frame':'Unchanged 6VGL Cartesian coordinates; no rotation, translation or alignment',
            'known_site_smoke_test':True,'blind_search':False,'tutorial_1IEP_parameters_reused':False}
    record.update({'model_variant':'tag_removed_JH1_domain','prepared_receptor_pdbqt':'outputs/receptor.pdbqt','ligand_pdbqt':'outputs/ligand.pdbqt','ligand_pdbqt_sha256':sha(root/'outputs/ligand.pdbqt'),'previous_binding_reused_as_validity_state':False})
    save(root/'records/box_binding.json',record)
    print(json.dumps(record,ensure_ascii=False,indent=2))
