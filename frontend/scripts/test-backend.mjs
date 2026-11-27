import {spawn} from 'node:child_process';

const child = spawn(process.env.PYTHON ?? 'python3', ['frontend/tests/fixture_backend.py'], {
  cwd: new URL('../../', import.meta.url),
  env: {...process.env, PYTHONPATH: '.'},
  stdio: 'inherit',
});
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
child.on('error', error => {
  console.error(error.message);
  process.exitCode = 1;
});
child.on('exit', code => {process.exitCode = code ?? 1;});
