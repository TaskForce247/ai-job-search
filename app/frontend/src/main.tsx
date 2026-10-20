import { createRoot } from 'react-dom/client';
import { initializeSession } from './api';
import { App } from './App';
import './styles.css';

void initializeSession().catch(() => { /* SessionGate displays authentication and connection failures. */ });
const root = document.getElementById('root');
if (!root) throw new Error('Application root is missing.');
createRoot(root).render(<App />);
