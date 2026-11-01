"""Standard-library contracts and safe local file handling; no scientific imports."""
import hashlib
import json
import math
from pathlib import Path

APPROVED_PROFILE_SHA='90dec5aa54cf592fefcac449b62547e24859e5b8636238c0818ab96eee45ba10'
REQUIRED_OUTPUTS={'receptor':'outputs/receptor.pdbqt','receptor_json':'outputs/receptor.json','prepared_pdb':'outputs/receptor_prepared.pdb',
 'selected_protein':'inputs/domain_experimental.pdb','terminal_model':'inputs/domain_terminal_model.pdb','reference':'outputs/reference.sdf',
 'ligand_sdf':'outputs/ligand.sdf','ligand':'outputs/ligand.pdbqt','ligand_roundtrip':'outputs/ligand_roundtrip.sdf','vina_box':'outputs/vina_box.txt'}
INPUT_NAMES={'structure':'original/6VGL.cif','sequence':'original/JAK2_O60674.fasta','handoff':'original/Stage2Handoff_01_updated_20261027.md','RXT_CCD':'original/RXT_CCD.cif'}


class PreparationError(Exception):
    def __init__(self,code,message,exit_code=1,**details):
        super().__init__(message);self.code=code;self.exit_code=exit_code;self.details=details


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('Duplicate JSON key: '+key)
            result[key]=value
        return result
    def invalid(value):raise ValueError('Non-finite JSON value: '+value)
    return json.loads(Path(path).read_text(),object_pairs_hook=pairs,parse_constant=invalid)


def write_json(path,value):
    path=Path(path);temporary=path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n');temporary.replace(path)


def safe(root,relative):
    if not isinstance(relative,str) or not relative:raise ValueError('Empty/non-string package path')
    path=Path(relative)
    if path.is_absolute() or '..' in path.parts or not (root/path).resolve().is_relative_to(root.resolve()):raise ValueError('Unsafe package path: '+relative)
    return root/path


def file_record(root,relative):
    path=safe(root,relative)
    return {'path':relative,'sha256':sha(path),'bytes':path.stat().st_size}


def write_manifest(root):
    excluded={'records/manifest.json','SHA256SUMS'}
    paths=sorted(p for p in root.rglob('*') if p.is_file() and p.relative_to(root).as_posix() not in excluded and '__pycache__' not in p.parts)
    record={'schema_version':'a4s.file_manifest.v1','job_id':read_json(root/'job.json')['job_id'],
            'files':[file_record(root,p.relative_to(root).as_posix()) for p in paths]}
    write_json(root/'records/manifest.json',record)
    (root/'SHA256SUMS').write_text(''.join(f'{r["sha256"]}  {r["path"]}\n' for r in record['files']))


def box_parameters(path):
    result={}
    for line in path.read_text().splitlines():
        key,value=line.split('=',1);key=key.strip()
        if key in result:raise ValueError('Duplicate box key: '+key)
        result[key]=float(value)
    if set(result)!={'center_x','center_y','center_z','size_x','size_y','size_z'} or not all(math.isfinite(v) for v in result.values()):raise ValueError('Invalid box fields')
    return result
