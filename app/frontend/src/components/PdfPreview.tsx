import { useEffect, useRef, useState } from 'react';
import type { PDFDocumentLoadingTask, PDFDocumentProxy, RenderTask } from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import { artifactBytes, artifactUrl } from '../api';
import type { DocumentContent } from '../types';
import { Alert, Loading } from './ui';

export function ReadDocument({ content }: { content: DocumentContent }) {
  return <div className="readable-document" lang={content.language === 'English' ? 'en' : undefined}>
    {content.blocks.length ? content.blocks.map((block) => <section key={block.id}>
      {block.heading && <h3>{block.heading}</h3>}
      {(block.organization || block.location) && <p><strong>{block.organization}</strong>{block.location && ` · ${block.location}`}</p>}
      {(block.start || block.end) && <p className="hint">{block.start}{block.end && ` — ${block.end}`}</p>}
      {block.text && <p className="preserve-lines">{block.text}</p>}
      {block.bullets.length > 0 && <ul>{block.bullets.map((bullet) => <li key={bullet.id}>{bullet.text}</li>)}</ul>}
    </section>) : <p>This document has no content blocks yet.</p>}
  </div>;
}

function PdfPage({ pdf, number, width }: { pdf: PDFDocumentProxy; number: number; width: number }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [error, setError] = useState<string>();
  useEffect(() => {
    let cancelled = false;
    let task: RenderTask | undefined;
    setError(undefined);
    void (async () => {
      try {
        const page = await pdf.getPage(number);
        if (cancelled || !canvas.current) return;
        const element = canvas.current;
        const context = element.getContext('2d');
        if (!context) throw new Error('Canvas rendering is unavailable in this browser.');
        const original = page.getViewport({ scale: 1 });
        const ratio = Math.min(window.devicePixelRatio || 1, 2);
        const viewport = page.getViewport({ scale: width / original.width });
        element.width = Math.floor(viewport.width * ratio);
        element.height = Math.floor(viewport.height * ratio);
        element.style.width = `${viewport.width}px`;
        element.style.height = `${viewport.height}px`;
        task = page.render({
          canvas: element, canvasContext: context, viewport,
          transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0],
        });
        await task.promise;
      } catch (cause) {
        if (!cancelled && !(cause instanceof Error && cause.name === 'RenderingCancelledException')) {
          setError(cause instanceof Error ? cause.message : 'This page could not be rendered.');
        }
      }
    })();
    return () => { cancelled = true; task?.cancel(); };
  }, [pdf, number, width]);
  return <div className="pdf-page">
    <p className="hint">Page {number} of {pdf.numPages}</p>
    {error ? <Alert tone="warning">{error} Use the readable document text below, or download the PDF.</Alert> :
      <canvas ref={canvas} role="img" aria-label={`PDF page ${number} of ${pdf.numPages}. Readable text is available below.`} />}
  </div>;
}

export function PdfPreview({ artifactId, stale, content }: {
  artifactId?: string | null; stale: boolean; content: DocumentContent;
}) {
  const container = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(560);
  const [loaded, setLoaded] = useState<{ id: string; pdf: PDFDocumentProxy }>();
  const [error, setError] = useState<string>();
  useEffect(() => {
    const element = container.current;
    if (!element || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setWidth(Math.max(120, Math.min(900, entry.contentRect.width - 24)));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    setLoaded(undefined); setError(undefined);
    if (!artifactId || stale) return;
    let cancelled = false;
    let task: PDFDocumentLoadingTask | undefined;
    const controller = new AbortController();
    void (async () => {
      try {
        const [pdfjs, bytes] = await Promise.all([import('pdfjs-dist'), artifactBytes(artifactId, controller.signal)]);
        if (cancelled) return;
        pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;
        task = pdfjs.getDocument({
          data: bytes, isEvalSupported: false, cMapUrl: '/pdfjs/cmaps/', cMapPacked: true,
          standardFontDataUrl: '/pdfjs/standard_fonts/', wasmUrl: '/pdfjs/wasm/',
        });
        const pdf = await task.promise;
        if (!cancelled) setLoaded({ id: artifactId, pdf });
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : 'The PDF could not be loaded.');
      }
    })();
    return () => {
      cancelled = true; controller.abort();
      if (task) void task.destroy().catch(() => { /* A cancelled preview has no remaining UI to update. */ });
    };
  }, [artifactId, stale]);

  const current = !stale && loaded && loaded.id === artifactId ? loaded.pdf : undefined;
  return <div className="pdf-preview" ref={container}>
    {stale ? <Alert tone="warning" title="PDF is out of date">
      Content has changed since the last compile. Save your edits, then choose “Resume checks.”
      The previous PDF is not a preview of your current edits.
      {artifactId && <p><a href={artifactUrl(artifactId)} download>Download previous PDF (out of date)</a></p>}
    </Alert> : !artifactId ? <Alert title="No compiled PDF yet">
      Save the document, then choose “Resume checks.” You can read and edit the text while you wait.
    </Alert> : error ? <Alert tone="warning" title="PDF preview unavailable">
      <p>{error}</p><p>Your document text is available below. <a href={artifactUrl(artifactId)} download>Download the PDF</a> to inspect it locally.</p>
    </Alert> : current ? <>
      <p role="status" className="hint">Compiled PDF · {current.numPages} {current.numPages === 1 ? 'page' : 'pages'} · rendered locally</p>
      <div className="pdf-pages" role="region" tabIndex={0} aria-label="PDF pages. Scroll to see every page.">
        {Array.from({ length: Math.min(current.numPages, 50) }, (_, i) =>
        <PdfPage key={`${artifactId}-${i}`} pdf={current} number={i + 1} width={width} />)}</div>
      {current.numPages > 50 && <Alert tone="warning">Preview is limited to 50 pages. Download the PDF to inspect every page.</Alert>}
      <a className="button button-secondary" href={artifactUrl(artifactId!)} download>Download current PDF</a>
    </> : <Loading label="Rendering PDF on this computer…" />}
    <details className="accessible-document"><summary>Read document text (accessible alternative)</summary>
      <ReadDocument content={content} /></details>
  </div>;
}
