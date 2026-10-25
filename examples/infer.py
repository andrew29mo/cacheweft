"""Independent optional ONNX Runtime CPU inference; never imported by the daemon."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import time
import numpy as np
import onnxruntime as ort


def evaluate(reference,paths):
    ort.disable_telemetry_events()
    # Synthetic normalized RGB gradient: model-execution consistency, not ImageNet accuracy.
    pixels=(np.arange(3*224*224,dtype=np.uint32)%256).astype(np.float32).reshape(1,3,224,224)/np.float32(255)
    x=(pixels-np.array([.485,.456,.406],dtype=np.float32).reshape(1,3,1,1))/np.array([.229,.224,.225],dtype=np.float32).reshape(1,3,1,1)
    x=np.ascontiguousarray(x,dtype='<f4')
    options=ort.SessionOptions();options.intra_op_num_threads=1;options.inter_op_num_threads=1
    options.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL
    baseline=None;outcomes=[]
    for name,path in [('reference',reference),*paths.items()]:
        start=time.monotonic();session=ort.InferenceSession(str(path),sess_options=options,providers=['CPUExecutionProvider']);load=time.monotonic()-start
        inputs=session.get_inputs()
        if len(inputs)!=1 or inputs[0].shape!=[1,3,224,224] or inputs[0].type!='tensor(float)':raise ValueError('Unexpected model input')
        feed={inputs[0].name:x};result=session.run(None,feed)
        if len(result)!=1 or result[0].shape!=(1,1000) or not np.isfinite(result[0]).all():raise ValueError('Bad model output')
        y=result[0]
        if baseline is None:baseline=y.copy()
        if not np.allclose(y,baseline,rtol=1e-5,atol=1e-6):raise ValueError('Inference differs from independent reference file')
        times=[]
        for _ in range(5):
            start=time.monotonic();repeated=session.run(None,feed)[0];times.append(time.monotonic()-start)
            if not np.allclose(repeated,baseline,rtol=1e-5,atol=1e-6):raise ValueError('Repeated inference changed')
        outcomes.append(dict(mode=name,model_sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest(),input_name=inputs[0].name,
            output_shape=list(y.shape),output_sha256=hashlib.sha256(y.astype('<f4').tobytes()).hexdigest(),
            max_abs_error=float(np.max(np.abs(y-baseline))),finite=True,rtol=1e-5,atol=1e-6,
            session_load_seconds=load,run_seconds=times,providers=session.get_providers()))
        del session
    return dict(onnxruntime=ort.__version__,numpy=np.__version__,packages={d.metadata['Name']:d.version for d in importlib.metadata.distributions()},
                input_sha256=hashlib.sha256(x.tobytes()).hexdigest(),input_shape=list(x.shape),input_kind='deterministic synthetic normalized RGB gradient; no accuracy claim',
                scope='separate inference process; CPUExecutionProvider; one thread; one warmup and five measured runs per file',cases=outcomes)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--reference',required=True);p.add_argument('--paths',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    result=evaluate(a.reference,json.loads(a.paths));Path(a.output).write_text(json.dumps(result,indent=2)+'\n')
