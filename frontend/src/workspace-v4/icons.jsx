const shapes = {
  wave: <path d="M2 14c3 0 3-6 6-6s3 6 6 6 3-6 6-6"/>,
  plus: <path d="M12 5v14M5 12h14"/>,
  menu: <path d="M4 6h16M4 12h16M4 18h16"/>,
  close: <path d="m6 6 12 12M18 6 6 18"/>,
  schema: <><rect x="9" y="2" width="6" height="5" rx="1"/><path d="M12 7v5M5 17v-5h14v5"/><rect x="2" y="17" width="6" height="5" rx="1"/><rect x="16" y="17" width="6" height="5" rx="1"/></>,
  file: <><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h6"/></>,
  download: <path d="M12 3v12m-5-5 5 5 5-5M5 19v2h14v-2"/>,
  'arrow-up': <path d="M12 19V5m-6 6 6-6 6 6"/>,
  'chevron-down': <path d="m6 9 6 6 6-6"/>,
  copy: <><rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h3"/></>,
  check: <path d="m5 12 4 4L19 6"/>,
  retry: <><path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 6a8 8 0 0 1 13 3M5 15a8 8 0 0 0 13 3"/></>,
  code: <path d="m8 7-5 5 5 5m8-10 5 5-5 5m-3-13-2 16"/>,
};
export function Icon({ name, size = 20, ...props }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>{shapes[name] || shapes.file}</svg>;
}
