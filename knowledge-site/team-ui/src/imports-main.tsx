import { createRoot } from 'react-dom/client';
import { Imports } from './Imports';
const root = document.getElementById('imports-root');
if (root) createRoot(root).render(<Imports csrf={root.dataset.csrf || ''} />);
