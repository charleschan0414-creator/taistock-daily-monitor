export function createRefreshController({
  documentRef = document,
  load,
  setIntervalFn = setInterval,
  clearIntervalFn = clearInterval,
}) {
  let timer = null;
  let pending = false;

  async function run() {
    if (pending) return;
    pending = true;
    try { await load(); }
    finally { pending = false; }
  }

  function sync() {
    if (documentRef.hidden) {
      if (timer !== null) clearIntervalFn(timer);
      timer = null;
    } else if (timer === null) {
      timer = setIntervalFn(run, 60_000);
    }
  }

  const visibilityChanged = () => {
    sync();
    if (!documentRef.hidden) run();
  };

  return {
    start() { documentRef.addEventListener('visibilitychange', visibilityChanged); sync(); run(); },
    manual: run,
    stop() {
      documentRef.removeEventListener('visibilitychange', visibilityChanged);
      if (timer !== null) clearIntervalFn(timer);
      timer = null;
    },
  };
}
