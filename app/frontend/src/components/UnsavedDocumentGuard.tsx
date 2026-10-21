import { useEffect } from 'react';
import { useBlocker } from 'react-router-dom';
import { Button, Modal } from './ui';

export function UnsavedDocumentGuard({ dirty }: { dirty: boolean }) {
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty &&
    (currentLocation.pathname !== nextLocation.pathname || currentLocation.search !== nextLocation.search));
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);
  return <Modal open={blocker.state === 'blocked'} title="Leave without saving your document?"
    onClose={() => blocker.state === 'blocked' && blocker.reset()}>
    <p>Your latest document edits are not saved. Stay here to save them, or leave and discard only the unsaved edits.
      Previously saved documents will not change.</p>
    <div className="actions">
      <Button variant="secondary" onClick={() => blocker.state === 'blocked' && blocker.reset()}>Stay & keep editing</Button>
      <Button variant="danger" onClick={() => blocker.state === 'blocked' && blocker.proceed()}>Discard edits & leave</Button>
    </div>
  </Modal>;
}
