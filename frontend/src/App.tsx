import React, { useState, useEffect } from 'react';
import { AlertCircle, CheckCircle, Clock, DollarSign, Users, Award } from 'lucide-react';

async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem("token"); // dev JWT

  const res = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {}),
    },
  });

   if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const AwardNominationApp = () => {
  const [currentUser, setCurrentUser] = useState(null);
  const [users, setUsers] = useState([]);
  const [nominations, setNominations] = useState([]);
  const [pendingApprovals, setPendingApprovals] = useState([]);
  const [activeTab, setActiveTab] = useState('nominate');
  const [loading, setLoading] = useState(false);
  
  // Nomination state
  const [selectedBeneficiary, setSelectedBeneficiary] = useState('');
  const [amount, setAmount] = useState('');
  const [description, setDescription] = useState('');
  const [submitStatus, setSubmitStatus] = useState(null);

  // Mock authentication - In production, use MSAL.js for Entra ID
  useEffect(() => {
    setCurrentUser({
      UserId: 1,
      FirstName: 'John',
      LastName: 'Doe',
      Title: 'Senior Manager',
      Email: 'john.doe@company.com'
    });
    
    loadUsers();
    loadNominations();
    loadPendingApprovals();
  }, []);

  const loadUsers = async () => {
    // Swagger shows GET /api/users
    const users = await apiFetch<any[]>("/api/users");
    setUsers(users);
  };

  const loadNominations = async () => {
    const history = await apiFetch<any[]>("/api/nominations/history");
    setNominations(history);
  };

  const loadPendingApprovals = async () => {
    const pending = await apiFetch<any[]>("/api/nominations/pending");
    setPendingApprovals(pending);
  };

  const handleSubmitNomination = async () => {
    if (!selectedBeneficiary || !amount || !description) {
      setSubmitStatus({ type: 'error', message: 'Please fill in all fields' });
      return;
    }

    setLoading(true);
    setSubmitStatus(null);

    try {
      const payload = {
        // Adjust these field names to match your backend model if needed
        BeneficiaryId: Number(selectedBeneficiary),
        DollarAmount: Number(amount),
        NominationDescription: description,
        NominatorId: currentUser?.UserId
      };
      
      await apiFetch("/api/nominations", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      
      setSubmitStatus({ type: 'success', message: 'Nomination submitted successfully!' });
      setSelectedBeneficiary('');
      setAmount('');
      setDescription('');
      
      setTimeout(() => {
        loadNominations();
        setSubmitStatus(null);
      }, 2000);
    } catch (error: any) {
      setSubmitStatus({ type: 'error', message: 'Failed to submit nomination. Please try again.' });
    } finally {
      setLoading(false);
    }
  };

  const handleApproval = async (nominationId: number, approved: boolean) => {
    setLoading(true);
    
    try {
      const action = approved ? "approve" : "reject";

      await apiFetch(`/api/nominations/${nominationId}/${action}`, {
      method: "POST",
      });

      await loadPendingApprovals();
      
      setPendingApprovals(prev => prev.filter(n => n.NominationId !== nominationId));
      alert(`Nomination ${approved ? 'approved' : 'rejected'} successfully!`);
    } catch (error) {
      alert('Failed to process approval. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const StatusBadge = ({ status }) => {
    const styles = {
      Pending: 'bg-yellow-100 text-yellow-800',
      Approved: 'bg-green-100 text-green-800',
      Paid: 'bg-blue-100 text-blue-800',
      Rejected: 'bg-red-100 text-red-800'
    };
    
    return (
      <span className={`px-3 py-1 rounded-full text-xs font-semibold ${styles[status] || 'bg-gray-100 text-gray-800'}`}>
        {status}
      </span>
    );
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100">
      <header className="bg-white shadow-sm border-b border-gray-200">
        <div className="max-w-7xl mx-auto px-4 py-4 sm:px-6 lg:px-8">
          <div className="flex justify-between items-center">
            <div className="flex items-center space-x-3">
              <Award className="w-8 h-8 text-indigo-600" />
              <div>
                <h1 className="text-2xl font-bold text-gray-900">Award Nomination System</h1>
                <p className="text-sm text-gray-600">Recognize outstanding achievements</p>
              </div>
            </div>
            {currentUser && (
              <div className="text-right">
                <p className="text-sm font-semibold text-gray-900">{currentUser.FirstName} {currentUser.LastName}</p>
                <p className="text-xs text-gray-600">{currentUser.Title}</p>
              </div>
            )}
          </div>
        </div>
      </header>

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 mt-6">
        <div className="bg-white rounded-lg shadow-sm p-1 flex space-x-1">
          <button
            onClick={() => setActiveTab('nominate')}
            className={`flex-1 py-3 px-4 rounded-md font-medium transition-colors ${
              activeTab === 'nominate'
                ? 'bg-indigo-600 text-white'
                : 'text-gray-700 hover:bg-gray-100'
            }`}
          >
            <Users className="w-5 h-5 inline-block mr-2" />
            Nominate Employee
          </button>
          <button
            onClick={() => setActiveTab('history')}
            className={`flex-1 py-3 px-4 rounded-md font-medium transition-colors ${
              activeTab === 'history'
                ? 'bg-indigo-600 text-white'
                : 'text-gray-700 hover:bg-gray-100'
            }`}
          >
            <Clock className="w-5 h-5 inline-block mr-2" />
            My Nominations
          </button>
          <button
            onClick={() => setActiveTab('approvals')}
            className={`flex-1 py-3 px-4 rounded-md font-medium transition-colors ${
              activeTab === 'approvals'
                ? 'bg-indigo-600 text-white'
                : 'text-gray-700 hover:bg-gray-100'
            }`}
          >
            <CheckCircle className="w-5 h-5 inline-block mr-2" />
            Pending Approvals
            {pendingApprovals.length > 0 && (
              <span className="ml-2 bg-red-500 text-white text-xs rounded-full px-2 py-1">
                {pendingApprovals.length}
              </span>
            )}
          </button>
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 mt-6 pb-12">
        {activeTab === 'nominate' && (
          <div className="bg-white rounded-lg shadow-md p-8">
            <h2 className="text-2xl font-bold text-gray-900 mb-6">Submit Award Nomination</h2>
            
            {submitStatus && (
              <div className={`mb-6 p-4 rounded-lg flex items-center ${
                submitStatus.type === 'success' ? 'bg-green-50 text-green-800' : 'bg-red-50 text-red-800'
              }`}>
                {submitStatus.type === 'success' ? (
                  <CheckCircle className="w-5 h-5 mr-2" />
                ) : (
                  <AlertCircle className="w-5 h-5 mr-2" />
                )}
                {submitStatus.message}
              </div>
            )}

            <div className="space-y-6">
              <div>
                <label className="block text-sm font-semibold text-gray-700 mb-2">
                  Select Employee to Nominate
                </label>
                <select
                  value={selectedBeneficiary}
                  onChange={(e) => setSelectedBeneficiary(e.target.value)}
                  className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
                >
                  <option value="">-- Select an employee --</option>
                  {users.map(user => (
                    <option key={user.UserId} value={user.UserId}>
                      {user.FirstName} {user.LastName} - {user.Title}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-sm font-semibold text-gray-700 mb-2">
                  Award Amount ($)
                </label>
                <div className="relative">
                  <DollarSign className="absolute left-3 top-3.5 w-5 h-5 text-gray-400" />
                  <input
                    type="number"
                    value={amount}
                    onChange={(e) => setAmount(e.target.value)}
                    min="50"
                    max="5000"
                    step="50"
                    placeholder="Enter amount (e.g., 500)"
                    className="w-full pl-10 pr-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
                  />
                </div>
                <p className="mt-1 text-xs text-gray-500">Amount must be between $50 and $5,000</p>
              </div>

              <div>
                <label className="block text-sm font-semibold text-gray-700 mb-2">
                  Nomination Description
                </label>
                <textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  rows={5}
                  placeholder="Describe the achievement or reason for this nomination..."
                  className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 resize-none"
                  maxLength={500}
                />
                <p className="mt-1 text-xs text-gray-500">{description.length}/500 characters</p>
              </div>

              <button
                onClick={handleSubmitNomination}
                disabled={loading}
                className="w-full bg-indigo-600 text-white py-3 px-6 rounded-lg font-semibold hover:bg-indigo-700 transition-colors disabled:bg-gray-400 disabled:cursor-not-allowed"
              >
                {loading ? 'Submitting...' : 'Submit Nomination'}
              </button>
            </div>
          </div>
        )}

        {activeTab === 'history' && (
          <div className="bg-white rounded-lg shadow-md p-8">
            <h2 className="text-2xl font-bold text-gray-900 mb-6">My Nomination History</h2>
            
            {nominations.length === 0 ? (
              <div className="text-center py-12">
                <Award className="w-16 h-16 text-gray-300 mx-auto mb-4" />
                <p className="text-gray-600">No nominations yet</p>
              </div>
            ) : (
              <div className="space-y-4">
                {nominations.map(nom => (
                  <div key={nom.NominationId} className="border border-gray-200 rounded-lg p-6 hover:shadow-md transition-shadow">
                    <div className="flex justify-between items-start mb-3">
                      <div>
                        <h3 className="text-lg font-semibold text-gray-900">{nom.BeneficiaryName}</h3>
                        <p className="text-sm text-gray-600">Nominated on {new Date(nom.NominationDate).toLocaleDateString()}</p>
                      </div>
                      <div className="text-right">
                        <p className="text-2xl font-bold text-indigo-600">${nom.DollarAmount}</p>
                        <StatusBadge status={nom.Status} />
                      </div>
                    </div>
                    <p className="text-gray-700">{nom.NominationDescription}</p>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {activeTab === 'approvals' && (
          <div className="bg-white rounded-lg shadow-md p-8">
            <h2 className="text-2xl font-bold text-gray-900 mb-6">Pending Approvals</h2>
            
            {pendingApprovals.length === 0 ? (
              <div className="text-center py-12">
                <CheckCircle className="w-16 h-16 text-gray-300 mx-auto mb-4" />
                <p className="text-gray-600">No pending approvals</p>
              </div>
            ) : (
              <div className="space-y-4">
                {pendingApprovals.map(nom => (
                  <div key={nom.NominationId} className="border border-gray-200 rounded-lg p-6">
                    <div className="flex justify-between items-start mb-4">
                      <div>
                        <h3 className="text-lg font-semibold text-gray-900">{nom.BeneficiaryName}</h3>
                        <p className="text-sm text-gray-600">Nominated by {nom.NominatorName} on {new Date(nom.NominationDate).toLocaleDateString()}</p>
                      </div>
                      <p className="text-2xl font-bold text-indigo-600">${nom.DollarAmount}</p>
                    </div>
                    <p className="text-gray-700 mb-4">{nom.NominationDescription}</p>
                    <div className="flex space-x-3">
                      <button
                        onClick={() => handleApproval(nom.NominationId, true)}
                        disabled={loading}
                        className="flex-1 bg-green-600 text-white py-2 px-4 rounded-lg font-medium hover:bg-green-700 transition-colors disabled:bg-gray-400"
                      >
                        Approve
                      </button>
                      <button
                        onClick={() => handleApproval(nom.NominationId, false)}
                        disabled={loading}
                        className="flex-1 bg-red-600 text-white py-2 px-4 rounded-lg font-medium hover:bg-red-700 transition-colors disabled:bg-gray-400"
                      >
                        Reject
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default AwardNominationApp;