import React, { useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { UploadCloud, FileText, AlertCircle, Loader } from 'lucide-react';
import { api } from '../services/api';

export const Upload: React.FC = () => {
  const [dragActive, setDragActive] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === 'dragenter' || e.type === 'dragover') {
      setDragActive(true);
    } else if (e.type === 'dragleave') {
      setDragActive(false);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);

    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      validateAndSetFile(e.dataTransfer.files[0]);
    }
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    e.preventDefault();
    if (e.target.files && e.target.files[0]) {
      validateAndSetFile(e.target.files[0]);
    }
  };

  const validateAndSetFile = (selectedFile: File) => {
    const validExtensions = ['.pdf', '.txt', '.json', '.csv', '.xml'];
    const fileExtension = selectedFile.name.substring(selectedFile.name.lastIndexOf('.')).toLowerCase();

    if (validExtensions.includes(fileExtension)) {
      setFile(selectedFile);
      setError(null);
    } else {
      setError(`Invalid file type. Supported formats: ${validExtensions.join(', ')}`);
      setFile(null);
    }
  };

  const handleButtonClick = () => {
    fileInputRef.current?.click();
  };

  const handleUpload = async () => {
    if (!file) return;

    setLoading(true);
    setError(null);

    try {
      const result = await api.uploadInvoice(file);
      navigate(`/processing/${result.invoice_id}`);
    } catch (err: any) {
      console.error(err);
      setError(err.response?.data?.detail || err.response?.data?.error || 'Failed to upload and start invoice processing.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="upload-container">
      <div className="upload-card">
        <h1 className="page-title">Ingest Invoice</h1>
        <p className="page-subtitle">Upload PDF, TXT, JSON, CSV, or XML invoices to start automated processing.</p>

        <form
          className={`drop-zone ${dragActive ? 'drag-active' : ''} ${file ? 'has-file' : ''}`}
          onDragEnter={handleDrag}
          onDragOver={handleDrag}
          onDragLeave={handleDrag}
          onDrop={handleDrop}
          onClick={handleButtonClick}
        >
          <input
            ref={fileInputRef}
            type="file"
            className="file-input-hidden"
            accept=".pdf,.txt,.json,.csv,.xml"
            onChange={handleChange}
          />

          {!file ? (
            <div className="drop-zone-content">
              <UploadCloud className="upload-icon animate-bounce" size={48} />
              <p className="upload-text">Drag and drop file here, or <span className="browse-link">browse</span></p>
              <p className="upload-limit">Supports PDF, TXT, JSON, CSV, XML up to 10MB</p>
            </div>
          ) : (
            <div className="file-preview-content">
              <FileText className="file-icon" size={48} />
              <div className="file-info">
                <p className="file-name">{file.name}</p>
                <p className="file-size">{(file.size / 1024).toFixed(1)} KB</p>
              </div>
            </div>
          )}
        </form>

        {error && (
          <div className="error-message">
            <AlertCircle size={20} />
            <span>{error}</span>
          </div>
        )}

        <div className="upload-actions">
          <button
            className="btn btn-primary"
            onClick={handleUpload}
            disabled={!file || loading}
          >
            {loading ? (
              <>
                <Loader className="spinner" size={20} />
                Processing Invoices...
              </>
            ) : (
              'Start Processing'
            )}
          </button>
        </div>
      </div>
    </div>
  );
};
